import cv2
import numpy as np
from skimage.transform._geometric import _umeyama

from face_swap.Face import Face
import face_swap.faceswap_utils as utils


class FaceGenerator:
    def __init__(self, generator_fun, input_size, tanh_fix=False, align=False):
        """
        :param generator_fun: takes a face and return an img plus optional mask
        :param tanh_fix: whether to realign prediction values to 0-1 scale before move to 256 RGB schema (needed exactly if models output is generated by tanh activation function)
        :param input_size: target size for the seed face before being fed to the generator fun
        """
        self.generator_fun = generator_fun
        self.tanh_fix = tanh_fix
        self.input_size = input_size
        self.align = align

    def generate(self, seed_face: Face=None, output_size=None):
        """
        Operates pre and post processing around the generation function
        :param seed_face:
        :param output_size:
        :return:
        """

        # pre-process face
        if self.align:
            face_img = utils._align_face(seed_face, size=self.input_size)
        else:
            face_img = seed_face.face_img
            face_img = cv2.resize(face_img, self.input_size)

        face_img = face_img / 255 * 2 - 1 if self.tanh_fix else face_img / 255.

        # generate face
        gen_face, face_mask = self.generator_fun(face_img)

        if self.align:
            eyes_center, angle, scale = utils.get_rotation_info(seed_face)
            m = cv2.getRotationMatrix2D(seed_face.get_face_center(absolute=False), -angle, 2 - scale)
            gen_face = cv2.warpAffine(gen_face, m, self.input_size,
                           flags=cv2.INTER_CUBIC)

        # post-process face
        gen_face = (gen_face + 1) * 255 / 2 if self.tanh_fix else gen_face * 255
        gen_face = np.clip(gen_face, 0, 255).astype(np.uint8)

        # if not specified we simply output with the same size of seed image
        if not output_size:
            output_size = seed_face.face_img.shape[:2][::-1]

        gen_face = cv2.resize(gen_face, output_size,
                              interpolation=cv2.INTER_CUBIC)
        if face_mask is not None:
            face_mask = cv2.resize(face_mask, output_size,
                                  interpolation=cv2.INTER_CUBIC)
        return gen_face, face_mask


def aue_generate_face(aue, face_img: np.array):
    """

    :param aue: autoencoder to use for the generation (call predict on it)
    :param face_img: img to feed to the generator
    :return:
    """
    gen_img = aue.predict(np.expand_dims(face_img, 0))[0]
    return gen_img, None


def gan_masked_generate_face(generator_fun, face_img: np.array):
    """
    Generated a face from the seed one considering a generator_fun which should output alpha mask and bgr results
    :param generator_fun: takes an image and returns alpha mask concatenated with bgr results
    :param face_img: img to feed to the generator
    :return:
    """

    gen_res = generator_fun(face_img)
    gen_mask = gen_res[:, :, 0]
    gen_bgr = gen_res[:, :, 1:]

    gen_mask = np.clip(gen_mask * 255, 0, 255).astype(np.uint8)
    # stack mask such as we have three channels
    gen_mask = np.stack([gen_mask, gen_mask, gen_mask], axis=2)
    return gen_bgr, gen_mask


def random_channel_shift(x, intensity=None, channel_axis=2):
    x = np.rollaxis(x, channel_axis, 0)
    min_x, max_x = np.min(x), np.max(x)
    if intensity is None:
        intensity = max_x/255*15.
    channel_images = [np.clip(x_channel + np.random.uniform(-intensity, intensity), min_x, max_x) for x_channel in x]
    x = np.stack(channel_images, axis=0)
    x = np.rollaxis(x, 0, channel_axis + 1)
    return x


def random_transform(img, rotation_range=90,
                     zoom_range=0.1, shift_range=0.1, random_flip=0.5,
                     channel_shift_intensity=0):
    h, w = img.shape[:2]
    # setup transformation factors
    rotation = np.random.uniform(-rotation_range, rotation_range)
    scale = np.random.uniform(1 - zoom_range, 1 + zoom_range)
    tx = np.random.uniform(-shift_range, shift_range) * w
    ty = np.random.uniform(-shift_range, shift_range) * h

    # setup transformation matrix
    mat = cv2.getRotationMatrix2D((w // 2, h // 2), rotation, scale)
    mat[:, 2] += (tx, ty)

    # warp affine
    result = cv2.warpAffine(img, mat, (w, h), borderMode=cv2.BORDER_REPLICATE)

    # optionally flip horizontally
    if np.random.random() < random_flip:
        result = result[:, ::-1]

    # optionally apply random channel shift:
    if channel_shift_intensity > 0:
        result = random_channel_shift(result, intensity=channel_shift_intensity)

    return result


def random_warp(img, mult_f=1):
    """
    Get pair of random warped images from aligned face image.
    For now assumes input image to be 256x256, and base returning size of 64x64
    :param img:
    :param mult_f: determines size of returned images (multiply each dimension, e.g. 2 for 128x128)
    :return:
    """
    assert img.shape == (256, 256, 3)
    #range_ = np.linspace(128 - 80, 128 + 80, 5)
    range_ = np.linspace(128 - 110, 128 + 110, 5)
    mapx = np.broadcast_to(range_, (5, 5))
    mapy = mapx.T

    mapx = mapx + np.random.normal(size=(5, 5), scale=5)
    mapy = mapy + np.random.normal(size=(5, 5), scale=5)

    interp_mapx = cv2.resize(mapx, (80*mult_f, 80*mult_f))[8*mult_f:72*mult_f, 8*mult_f:72*mult_f].astype('float32')
    interp_mapy = cv2.resize(mapy, (80*mult_f, 80*mult_f))[8*mult_f:72*mult_f, 8*mult_f:72*mult_f].astype('float32')

    warped_image = cv2.remap(img, interp_mapx, interp_mapy, cv2.INTER_LINEAR)

    src_points = np.stack([mapx.ravel(), mapy.ravel()], axis=-1)
    dst_points = np.mgrid[0:65*mult_f:16*mult_f, 0:65*mult_f:16*mult_f].T.reshape(-1, 2)
    mat = _umeyama(src_points, dst_points, True)[0:2]

    target_image = cv2.warpAffine(img, mat, (64*mult_f, 64*mult_f))

    target_image = cv2.resize(target_image, (64*mult_f, 64*mult_f))
    warped_image = cv2.resize(warped_image, (64*mult_f, 64*mult_f))

    return warped_image, target_image
