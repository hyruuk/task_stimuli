import os, sys, datetime, time
import numpy as np
from psychopy import visual, core, data, logging, event
from .ellipse import Ellipse

from ..tasks.task_base import Task
from . import config

EYETRACKING_OUTDIR = 'output/eyetracking/'
CALIBRATE_HOTKEY = 'c'

MARKER_SIZE = 50
MARKER_FILL_COLOR = (.8,0,.5)
MARKER_DURATION_FRAMES = 240
MARKER_POSITIONS = np.asarray([(.25, .5), (0, .5), (0., 1.), (.5, 1.), (1., 1.),
    (1., .5), (1., 0.), (.5, 0.), (0., 0.), (.75, .5)])

# number of frames to eliminate at start and end of marker
CALIBRATION_LEAD_IN = 20
CALIBRATION_LEAD_OUT = 20
# remove pupil samples with low confidence
PUPIL_CONFIDENCE_THRESHOLD = .4

class EyetrackerCalibration(Task):

    def __init__(self,eyetracker, *args,**kwargs):
        kwargs['use_eyetracking'] = True
        super().__init__(**kwargs)
        self.eyetracker = eyetracker

    def instructions(self, exp_win, ctl_win):
        instruction_text = """We're going to calibrate the eyetracker.
Please look at the markers that appear on the screen."""
        screen_text = visual.TextStim(
            exp_win, text=instruction_text,
            alignHoriz="center", color = 'white')

        for frameN in range(config.FRAME_RATE * 5):
            screen_text.draw(exp_win)
            screen_text.draw(ctl_win)
            yield()

    def run(self, exp_win, ctl_win, order='random', marker_fill_color=MARKER_FILL_COLOR):
        while True:
            allKeys = event.getKeys([CALIBRATE_HOTKEY])
            start_calibration = False
            for key in allKeys:
                if key == CALIBRATE_HOTKEY:
                    start_calibration = True
            if start_calibration:
                break
            yield

        window_size_frame = exp_win.size-MARKER_SIZE*2
        print(window_size_frame)
        circle_marker = visual.Circle(
            exp_win, edges=64, units='pixels',
            lineColor=None,fillColor=marker_fill_color,
            autoLog=False)

        random_order = np.random.permutation(np.arange(len(MARKER_POSITIONS)))

        all_refs_per_flip = []
        all_pupils_normpos = []

        radius_anim = np.hstack([np.linspace(MARKER_SIZE,0,MARKER_DURATION_FRAMES/2),
                                 np.linspace(0,MARKER_SIZE,MARKER_DURATION_FRAMES/2)])

        exp_win.logOnFlip(level=logging.EXP,msg='eyetracker_calibration: starting at %f'%time.time())
        for site_id in random_order:
            marker_pos = MARKER_POSITIONS[site_id]
            pos = (marker_pos-.5)*window_size_frame
            circle_marker.pos = pos
            exp_win.logOnFlip(level=logging.EXP,
                msg="calibrate_position,%d,%d,%d,%d"%(marker_pos[0],marker_pos[1], pos[0],pos[1]))
            for f,r in enumerate(radius_anim):
                circle_marker.radius = r
                circle_marker.draw(exp_win)
                circle_marker.draw(ctl_win)

                pupil = self.eyetracker.get_pupil()
                exp_win.logOnFlip(level=logging.EXP,
                    msg="pupil: pos=(%f,%f), diameter=%d"%(pupil['norm_pos']+(pupil['diameter'],)))
                if f > CALIBRATION_LEAD_IN and f < len(radius_anim)-CALIBRATION_LEAD_OUT:
                    if pupil['confidence'] > PUPIL_CONFIDENCE_THRESHOLD:
                        all_refs_per_flip.append(pos/exp_win.size*2)
                        all_pupils_normpos.append(pupil['norm_pos'])
                yield
        self.eyetracker.calibrate(all_refs_per_flip, all_pupils_normpos)

import threading
import cv2
sys.path.append('/home/basile/data/src/pupil/pupil_src/shared_modules/')
from pupil_detectors.detector_2d import Detector_2D
from pupil_detectors.detector_3d import Detector_3D
import calibration_routines.calibrate
from methods import Roi


import v4l2capture
import select
import skvideo.io

from . import config

class Frame(object):
    """docstring of Frame"""
    def __init__(self, timestamp, frame, index):
        self._frame = frame
        self.timestamp = timestamp
        self.index = index
        self._img = None
        self._gray = None
        if self._frame.ndim < 3:
            self._gray = self._frame
        self.jpeg_buffer = None
        self.yuv_buffer = None
        self.height, self.width = frame.shape[:2]

    def copy(self):
        return Frame(self.timestamp, self._frame, self.index)

    @property
    def img(self):
        return self._frame

    @property
    def bgr(self):
        return self.img

    @property
    def gray(self):
        if self._gray is None:
            self._gray = self._frame.mean(-1).astype(self._frame.dtype)
        return self._gray

class EyeTracker(threading.Thread):

    def __init__(self, ctl_win, video_input=0, roi=None, detector='2d'):
        super(EyeTracker, self).__init__()
        self.ctl_win = ctl_win
        self.eye_win = visual.Window(**config.EYE_WINDOW)

        self._videocap = cv2.VideoCapture(video_input)
        #self._videocap = v4l2capture.Video_device(video_input)
        ret, self.cv_frame = self._videocap.read()

        self._width = int(self._videocap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._height = int(self._videocap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        #self._width, self._height = self._videocap.set_format(640, 480, fourcc='GREY')
        #self._width, self._height = 720,480
        #self._videocap.create_buffers(30)
        #self._videocap.queue_all_buffers()
        self._frame_index = -1

        print(self._width, self._height)
        self.roi = roi
        if roi is None:
            self.roi = Roi((self._width, self._height))

            #self.roi = Roi((75,50,self._width-150, self._height-100))
            print(self.roi.get())

        roi_width = self.roi.get()[4][0]
        roi_height = self.roi.get()[4][1]
        pos_roi_x =  self.roi.get()[0]
        pos_roi_y =  self.roi.get()[1]

        self._image_stim = visual.ImageStim(
            self.eye_win,
            size=(self._width,self._height),
            units='pixels',
            autoLog=False)
        self._roi_stim = visual.Rect(
            self.eye_win,
            pos=(pos_roi_x+(roi_width-self._width)/2,
                 pos_roi_y+(roi_height-self._height)/2),
            width=roi_width, height=roi_height,
            units='pixels',
            lineColor=(1,0,0),fillColor=None,
            autoLog=False)
        self._pupil_stim = Ellipse(
            self.eye_win,
            radius=0,
            radius2=0,
            units='pixels',
            lineColor=(1,0,0),fillColor=None, lineWidth=1,
            autoLog=False)

        self._gazepoint_stim = visual.Circle(
            self.ctl_win,
            radius=30,
            units='pixels',
            lineColor=(1,0,0),fillColor=None,
            autoLog=False)

        if detector == '2d':
            self._detector = Detector_2D()
        elif detector == '3d':
            self._detector = Detector_3D()

        #TODO: load settings and/or GUI
        settings = self._detector.get_settings()
        settings["pupil_size_min"] = 50
        settings["pupil_size_max"] = 200
        settings["intensity_range"] = 10
        #settings["ellipse_roundness_ratio"] = .01
        print(self._detector.get_settings())

        self.pupils = None
        self.stoprequest = threading.Event()
        self.lock = threading.Lock()

    def join(self, timeout=None):
        self.stoprequest.set()
        super(EyeTracker, self).join(timeout)
        self.release()

    def get_pupil(self):
        with self.lock:
            return self.pupils

    def run(self):
        eyetracking_output_name = os.path.join(
            EYETRACKING_OUTDIR,
            datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S'))
        self._videowriter = skvideo.io.FFmpegWriter(
            eyetracking_output_name+'.mp4',
            {'-pix_fmt':'gray','-r':'30'},
            {'-pix_fmt':'gray','-c:v': 'libx264','-r':'30'})
        #self._videocap.start()
        with open(eyetracking_output_name+'.log', 'w') as eyetracking_outfile:
            while not self.stoprequest.isSet():
                self.update()
                output = '%f, %f, %f, %f, %f' %((self._capture_timestamp,)+
                    self.pupils['norm_pos']+(self.pupils['diameter'], self.pupils['confidence']))
                if hasattr(self,'map_fn'):
                    output += ', %f, %f'%(self.pos_cal)
                eyetracking_outfile.write(output+'\n')
                self.draw()

    def update(self):
        #capture
        ret, self.cv_frame = self._videocap.read()
        #self._capture_timestamp = self._videocap.get(cv2.CAP_PROP_POS_MSEC)
        #select.select((self._videocap,), (), ())
        #raw_frame = self._videocap.read_and_queue()
        self._frame_index += 1
        self._capture_timestamp = time.time()
        #self.cv_frame = np.frombuffer(raw_frame,dtype=np.uint8).reshape(self._height, self._width, 3).copy()
        #detect
        p_frame = Frame(0, self.cv_frame, 0)
        pupils = self._detector.detect(p_frame, self.roi, False)
        with self.lock:
            self.pupils = pupils
            if hasattr(self,'map_fn'):
                self.pos_cal = self.map_fn(self.pupils['norm_pos'])
        self._videowriter.writeFrame(p_frame.gray)

    def draw(self):
        #render image roi pupil
        self._image_stim.setImage(self.cv_frame/128-1)
        self._image_stim.draw(self.eye_win)

        self._roi_stim.draw(self.eye_win)

        if self.pupils['confidence'] > 0:
            #print(self.pupils)
            center = self.pupils['ellipse']['center']
            self._pupil_stim.pos = (center[0]-self._width/2, center[1]-self._height/2)
            self._pupil_stim.radius = self.pupils['ellipse']['axes'][0]/2
            self._pupil_stim.radius2 = self.pupils['ellipse']['axes'][1]/2
            self._pupil_stim.ori = -self.pupils['ellipse']['angle']
            self._pupil_stim.draw(self.eye_win)

        self.eye_win.flip()

    def draw_gazepoint(self,win):
        if hasattr(self,'map_fn'):
            with self.lock:
                pos_cal = self.pos_cal
            if np.isnan(pos_cal[0]) or np.isnan(pos_cal[0]):
                return
            self._gazepoint_stim.pos = (int(pos_cal[0]/2*self.ctl_win.size[0]),
                                        int(pos_cal[1]/2*self.ctl_win.size[1]))
            #self._gazepoint_stim.radius = self.pupils['diameter']/2
            #print(self._gazepoint_stim.pos, self._gazepoint_stim.radius)
            self._gazepoint_stim.draw(win)

    def release(self):
        self._videocap.release()
        #self._videocap.close()
        self._videowriter.close()

    def calibrate(self, all_refs_per_flip, all_pupils_normpos):
        if len(all_refs_per_flip) < 100:
            return
        all_points = np.hstack([all_pupils_normpos, all_refs_per_flip])
        np.savetxt('calibration_data.txt',all_points, fmt='%f')
        self.map_fn, inliers, self.calib_params = calibration_routines.calibrate.calibrate_2d_polynomial(
            all_points,
            (self._width,self._height),
            binocular=False)
        print(self.calib_params)


#window = visual.Window(monitor=0,fullscr=True)
#lastLog = logging.LogFile("lastRun.log", level=logging.INFO, filemode='w')
#calibrate(window)
#logging.flush()