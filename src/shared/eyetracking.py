import os, sys, datetime, time
import threading

from .zmq_tools import *
import msgpack

import numpy as np
from psychopy import visual, core, data, logging, event
from .ellipse import Ellipse

from ..tasks.task_base import Task
from . import config

INSTRUCTION_DURATION = 5

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

    def __init__(self,eyetracker, order='random', marker_fill_color=MARKER_FILL_COLOR, **kwargs):
        kwargs['use_eyetracking'] = True
        self.order = order
        self.marker_fill_color = marker_fill_color
        super().__init__(**kwargs)
        self.eyetracker = eyetracker

    def instructions(self, exp_win, ctl_win):
        instruction_text = """We're going to calibrate the eyetracker.
Please look at the markers that appear on the screen."""
        screen_text = visual.TextStim(
            exp_win, text=instruction_text,
            alignHoriz="center", color = 'white')

        for frameN in range(config.FRAME_RATE * INSTRUCTION_DURATION):
            screen_text.draw(exp_win)
            screen_text.draw(ctl_win)
            yield()

    def _run(self, exp_win, ctl_win):
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
            lineColor=None,fillColor=self.marker_fill_color,
            autoLog=False)

        random_order = np.random.permutation(np.arange(len(MARKER_POSITIONS)))

        all_refs_per_flip = []
        all_pupils = []

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
                if pupil is None:
                    return
                exp_win.logOnFlip(level=logging.EXP,
                    msg="pupil: pos=(%f,%f), diameter=%d"%tuple(pupil['norm_pos']+[pupil['diameter']]))
                if f > CALIBRATION_LEAD_IN and f < len(radius_anim)-CALIBRATION_LEAD_OUT:
                    if pupil['confidence'] > PUPIL_CONFIDENCE_THRESHOLD:
                        pos_decenter = (pos/exp_win.size*2).tolist()
                        ref = {
                            'norm_pos': pos_decenter,
                            'screen_pos': pos_decenter,
                            'timestamp': pupil['timestamp']}
                        all_refs_per_flip.append(ref)
                        all_pupils.append(pupil)
                yield
        self.eyetracker.calibrate(all_pupils, all_refs_per_flip, exp_win.size)

from subprocess import Popen

class EyeTrackerClient(threading.Thread):

    def __init__(self, output_path, output_fname_base):
        super(EyeTrackerClient, self).__init__()
        self.stoprequest = threading.Event()
        self.lock = threading.Lock()

        self.pupil = None
        self.gaze = None

        self.output_path = output_path
        self.output_fname_base = output_fname_base

        self._pupil_process = Popen([
            'python3',
            os.path.join(os.environ['PUPIL_PATH'],'pupil_src','main.py'),
            'service'])

        self._ctx = zmq.Context()
        self._req_socket = self._ctx.socket(zmq.REQ)
        self._req_socket.connect('tcp://localhost:50020')

        print(self.send_recv_notification({
            'subject':'eye_process.should_start.0',
            'eye_id':0, 'args':{}}))
        # wait for the whole schmilblick to boot
        time.sleep(4)

    def send_recv_notification(self, n):
        # REQ REP requirese lock step communication with multipart msg (topic,msgpack_encoded dict)
        self._req_socket.send_multipart((bytes('notify.%s'%n['subject'],'utf-8'), msgpack.dumps(n)))
        return self._req_socket.recv()

    def get_pupil_timestamp(self):
        self._req_socket.send('t') #see Pupil Remote Plugin for details
        return float(self._req_socket.recv())

    def join(self, timeout=None):
        self.stoprequest.set()
        # leave time to exit the infinite loop
        time.sleep(.2)
        # stop recording
        self.send_recv_notification({'subject':'recording.stopped'})

        self.send_recv_notification({'subject':'service_process.should_stop'})
        self._pupil_process.wait(timeout)
        eye_video_file = os.path.join(self.output_path, 'eye0.mp4')
        timestamps_file = os.path.join(self.output_path, 'eye0_timestamps.npy')
        # rename file in case we rerun the software
        os.rename(eye_video_file, os.path.join(self.output_path,'%s_eye0.mp4'%self.output_fname_base))
        os.rename(timestamps_file, os.path.join(self.output_path,'%s_eye0_timestamps.npy'%self.output_fname_base))
        super(EyeTrackerClient, self).join(timeout)

    def run(self):
        gaze_output_name = os.path.join(self.output_path, '%s_gaze0.pldata'%self.output_fname_base)
        gaze_fh = open(gaze_output_name,'wb')
        recording_started = False

        def write_msgpack_serialized(fh, data):
            ser_data = msgpack.packb(data, use_bin_type=True)
            fh.write(ser_data)

        self._req_socket.send_string('SUB_PORT')
        ipc_sub_port = int(self._req_socket.recv())
        monitor = Msg_Receiver(self._ctx,'tcp://localhost:%d'%ipc_sub_port,topics=('gaze','pupil'))
        while not self.stoprequest.isSet():
            topic, tmp = monitor.recv()
            if not recording_started:
                #start recording the video of the eye
                logging.exp('first pupil received with timestamp %s'%tmp['timestamp'])
                self.send_recv_notification({
                    'subject':'recording.started',
                    'record_eye':True,
                    'rec_path': self.output_path,
                    'compression':None,
                    'args':{'video_stream':{"codec": "mpeg4", "bit_rate": 15000 * 10e3}}
                    })
                recording_started=True
            with self.lock:
                if topic.startswith('pupil'):
                    self.pupil = tmp
                if topic.startswith('gaze'):
                    self.gaze = tmp
            if topic.startswith('gaze'):
                write_msgpack_serialized(gaze_fh, tmp)

        print('eyetracker listener: stopping')


    def get_pupil(self):
        with self.lock:
            return self.pupil

    def get_gaze(self):
        with self.lock:
            return self.gaze

    def calibrate(self, pupil_list, ref_list, frame_size):
        if len(pupil_list) < 100:
            # TODO: log
            return

        self.send_recv_notification({
            'subject':'start_plugin',
            'name':'Mock_Calibration',
            'args':{'frame_size': frame_size.tolist()}})

        self.send_recv_notification({
            'subject':'calibrate.from_external_data',
            'pupil_list':pupil_list,
            'ref_list':ref_list})



class GazeDrawer():

    def __init__(self, win):

        self.win = win
        self._gazepoint_stim = visual.Circle(
            self.win,
            radius=30,
            units='pixels',
            lineColor=(1,0,0),fillColor=None, lineWidth=2,
            autoLog=False)

    def draw_gazepoint(self, gaze):
        pos = gaze['norm_pos']
        self._gazepoint_stim.pos = (int(pos[0]/2*self.win.size[0]),
                                   int(pos[1]/2*self.win.size[1]))
        #self._gazepoint_stim.radius = self.pupils['diameter']/2
        #print(self._gazepoint_stim.pos, self._gazepoint_stim.radius)
        self._gazepoint_stim.draw(self.win)



def read_pl_data(fname):
    with open(fname, "rb") as fh:
        for data in msgpack.Unpacker(fh, raw=False, use_list=False):
            yield(data)





















import cv2
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

    def __init__(self, ctl_win, output_dir, video_input=0, roi=None, detector='2d'):
        super(EyeTracker, self).__init__()
        self.ctl_win = ctl_win
        self.output_dir = output_dir
        self.eye_win = visual.Window(**config.EYE_WINDOW)
        self.eye_win.winHandle.set_caption('Eyetracking')
        self.mouse = event.Mouse(win=self.eye_win)

        #self._videocap = cv2.VideoCapture(video_input)
        self._videocap = v4l2capture.Video_device(video_input)
        #ret, self.cv_frame = self._videocap.read()

        #self._width = int(self._videocap.get(cv2.CAP_PROP_FRAME_WIDTH))
        #self._height = int(self._videocap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._width, self._height = self._videocap.set_format(640, 480, fourcc='GREY')
        self._videocap.create_buffers(30)
        self._videocap.queue_all_buffers()
        self._frame_index = -1

        print(self._width, self._height)
        if roi is None:
            roi = (0, 0, self._width, self._height)
        self._roi_stim = None
        self.set_roi(*roi)

        self._image_stim = visual.ImageStim(
            self.eye_win,
            size=(self._width,self._height),
            units='pixels',
            autoLog=False)

        self._pupil_stim = Ellipse(
            self.eye_win,
            radius=0,
            radius2=0,
            lineColor=(1,0,0),fillColor=None, lineWidth=2,
            units='pixels',
            autoLog=False)

        self._gazepoint_stim = visual.Circle(
            self.ctl_win,
            radius=30,
            units='pixels',
            lineColor=(1,0,0),fillColor=None, lineWidth=2,
            autoLog=False)

        if detector == '2d':
            self._detector = Detector_2D()
        elif detector == '3d':
            self._detector = Detector_3D()

        #TODO: load settings and/or GUI
        settings = self._detector.get_settings()
        settings["pupil_size_min"] = 30
        settings["pupil_size_max"] = 200
        #settings["intensity_range"] = 10
        #settings["ellipse_roundness_ratio"] = .1
        print(self._detector.get_settings())

        self.pupils = None
        self.stoprequest = threading.Event()
        self.lock = threading.Lock()

    def set_roi(self, lX, lY, uX, uY):
        print('set roi: %d %d %d %d'%(lX, lY, uX, uY))
        roi_width = uX-lX
        roi_height = uY-lY
        if roi_width<=0 or roi_height<=0:
            return
        self.roi = Roi((roi_width, roi_height))
        self.roi.set((lX, lY, uX, uY, (roi_width, roi_height)))
        if self._roi_stim is None:
            self._roi_stim = visual.Rect(
                self.eye_win,
                units='pixels',
                lineColor=(1,0,0),fillColor=None,
                autoLog=False)

        self._roi_stim.pos = (
            lX+(roi_width-self._width)/2,
            lY+(roi_height-self._height)/2),
        self._roi_stim.width = roi_width
        self._roi_stim.height = roi_height

    def join(self, timeout=None):
        self.stoprequest.set()
        super(EyeTracker, self).join(timeout)
        self.release()

    def get_pupil(self):
        with self.lock:
            return self.pupils

    def run(self):
        eyetracking_output_name = os.path.join(
            self.output_dir,
            datetime.datetime.now().strftime('eyetrack_%Y%m%d_%H%M%S'))
        self._videowriter = skvideo.io.FFmpegWriter(
            eyetracking_output_name+'.mkv',
            {'-pix_fmt':'gray','-r':'30'},
            #{'-c:v': 'mpeg4','-r':'30','-pix_fmt':'yuv420p'}
            {'-pix_fmt':'gray','-c:v': 'ffv1','-r':'30'}
            )
        self._videocap.start()
        mouse_pressed = False
        with open(eyetracking_output_name+'.log', 'w') as eyetracking_outfile:
            while not self.stoprequest.isSet():
                self.update()
                output = '%f, %f, %f, %f, %f' %((self._capture_timestamp,)+
                    self.pupils['norm_pos']+(self.pupils['diameter'], self.pupils['confidence']))
                if hasattr(self,'map_fn'):
                    output += ', %f, %f'%(self.pos_cal)
                eyetracking_outfile.write(output+'\n')

                ## update ROI
                if any(self.mouse.getPressed()):
                    if self.mouse.isPressedIn(self._image_stim):
                        if not mouse_pressed:
                            init_pos = self.mouse.getPos()
                        mouse_pressed = True
                else:
                    if mouse_pressed:
                        end_pos = self.mouse.getPos()
                        mouse_pressed = False
                        coords = (np.sort([init_pos, end_pos],0)+(self._width/2,self._height/2)).astype(np.int)
                        coords = np.clip(coords, (0,0), (self._width,self._height))
                        self.set_roi(coords[0,0], coords[0,1], coords[1,0], coords[1,1])
                self.draw()

    def update(self):
        #capture
        #ret, self.cv_frame = self._videocap.read()
        #self._capture_timestamp = self._videocap.get(cv2.CAP_PROP_POS_MSEC)
        select.select((self._videocap,), (), ())
        raw_frame = self._videocap.read_and_queue()
        self._frame_index += 1
        self._capture_timestamp = time.time()
        self.cv_frame = np.frombuffer(raw_frame,dtype=np.uint8).reshape(self._height, self._width).copy()
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
        self._image_stim.image = self.cv_frame/128.-1
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
        #self._videocap.release()
        self._videocap.close()
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
