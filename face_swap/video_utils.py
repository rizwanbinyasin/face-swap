from os.path import join
import numpy as np
import cv2
from tqdm import tqdm

from face_swap.Face import Face

# Option of relying on MoviePy (http://zulko.github.io/moviepy/index.html)


def convert_video_to_video(video_path: str, out_path: str, frame_edit_fun,
                  codec='mp4v'):
    # "Load" input video
    input_video = cv2.VideoCapture(video_path)

    # Match source video features.
    fps = input_video.get(cv2.CAP_PROP_FPS)
    size = (
        int(input_video.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(input_video.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )

    # Define the codec and create VideoWriter object
    fourcc = cv2.VideoWriter_fourcc(*codec)
    out = cv2.VideoWriter(out_path, fourcc, fps, size)

    # Process frame by frame

    # some codecs don't support this, so in such cases we need to rollback to base looping
    nb_frames = int(input_video.get(cv2.CAP_PROP_FRAME_COUNT))
    if nb_frames and nb_frames > 0:
        for _ in tqdm(range(nb_frames)):
            _, frame = input_video.read()

            frame = frame_edit_fun(frame)
            out.write(frame)
    else:
        while input_video.isOpened():
            ret, frame = input_video.read()
            if ret:
                frame = frame_edit_fun(frame)
                out.write(frame)
            else:
                break

    # Release everything if job is finished
    input_video.release()
    out.release()


class VideoConverter:
    def __init__(self, use_kalman_filter=False, bbox_mavg_coef=0.35):
        self.use_kalman_filter = use_kalman_filter
        if use_kalman_filter:
            self.noise_coef = 5e-3  # Increase by 10x if tracking is slow.
            self.kf0 = kalmanfilter_init(self.noise_coef)
            self.kf1 = kalmanfilter_init(self.noise_coef)

        self.bbox_mavg_coef = bbox_mavg_coef
        self.prev_coords = (0, 0, 0, 0)
        self.frames = 0
        self.reset_state = True

    def reset(self):
        if not self.reset_state:
            if self.use_kalman_filter:
                self.kf0 = kalmanfilter_init(self.noise_coef)
                self.kf1 = kalmanfilter_init(self.noise_coef)
            self.prev_coords = (0, 0, 0, 0)
            self.frames = 0
            self.reset_state = True

    def get_center(self, face: Face):
        coords = face.rect.get_coords()

        # adjust coords only if we have previous valid values
        if self.frames != 0:
            coords = self._get_smoothed_coord(coords, face.img.shape)
            self.prev_coords = coords
        else:
            self.prev_coords = coords
            _ = self._get_smoothed_coord(coords, face.img.shape)
        self.frames += 1
        top, right, bottom, left = coords
        return left + (right - left) // 2, top + (bottom - top) // 2

    def _get_smoothed_coord(self, coords: tuple, shape):
        # simply rely on moving average
        if not self.use_kalman_filter:
            coords = tuple([
                # adjust each coordinate based on coefficients and prev coordinate
                int(self.bbox_mavg_coef * prev_coord + (1 - self.bbox_mavg_coef) * curr_coord)
                for curr_coord, prev_coord in zip(coords, self.prev_coords)
            ])
        # use kalman filter
        else:
            x0, x1, y0, y1 = coords
            prev_x0, prev_y0, prev_x1, prev_y1 = self.prev_coords
            x0y0 = np.array([x0, y0]).astype(np.float32)
            x1y1 = np.array([x1, y1]).astype(np.float32)
            if self.frames == 0:
                for i in range(200):
                    self.kf0.predict()
                    self.kf1.predict()
            self.kf0.correct(x0y0)
            pred_x0y0 = self.kf0.predict()
            self.kf1.correct(x1y1)
            pred_x1y1 = self.kf1.predict()
            x0 = np.max([0, pred_x0y0[0][0]]).astype(np.int)
            x1 = np.min([shape[0], pred_x1y1[0][0]]).astype(np.int)
            y0 = np.max([0, pred_x0y0[1][0]]).astype(np.int)
            y1 = np.min([shape[1], pred_x1y1[1][0]]).astype(np.int)
            if x0 == x1 or y0 == y1:
                x0, y0, x1, y1 = prev_x0, prev_y0, prev_x1, prev_y1
            coords = x0, x1, y0, y1
        return coords


def kalmanfilter_init(noise_coef):
    kf = cv2.KalmanFilter(4,2)
    kf.measurementMatrix = np.array([[1,0,0,0],[0,1,0,0]], np.float32)
    kf.transitionMatrix = np.array([[1,0,1,0],[0,1,0,1],[0,0,1,0],[0,0,0,1]], np.float32)
    kf.processNoiseCov = noise_coef * np.array([[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]], np.float32)
    return kf
