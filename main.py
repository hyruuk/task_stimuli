from psychopy import visual, logging, event

from src.shared import config, fmri, eyetracking
from src.tasks import images, video, memory

def main():
    ctl_win = visual.Window(**config.CTL_WINDOW)
    exp_win = visual.Window(**config.EXP_WINDOW)

    if config.EYETRACKING:
        roi = eyetracking.Roi(config.EYETRACKING_ROI[-1])
        roi.set(config.EYETRACKING_ROI)
        eyetracker = eyetracking.EyeTracker(
            ctl_win,
            roi=roi,
            video_input="/dev/video1",
            detector='2d')
        eyetracker.start()

    # list of tasks to be ran in a session
    all_tasks = [
        #eyetracking.EyetrackerCalibration(eyetracker),
        memory.ImagePosition('data/memory/stimuli.csv', use_fmri=True, use_eyetracking=True),
        video.SingleVideo('data/videos/Climbing Ice - The Iceland Trifecta-79s5BD0301o.mkv',use_fmri=True, use_eyetracking=True),
        video.SingleVideo('data/videos/Inscapes-67962604.mp4',use_fmri=True, use_eyetracking=True),
        images.Images('data/images/test_conditions.csv',use_fmri=True, use_eyetracking=True)
        ]


    for task in all_tasks:

        # ensure to clear the screen if task aborted
        exp_win.flip()
        ctl_win.flip()

        use_eyetracking = False
        if config.EYETRACKING and task.use_eyetracking:
            use_eyetracking = True

        #preload task files (eg. video)
        task.preload(exp_win)

        allKeys = []

        while True:

            for _ in task.run(exp_win, ctl_win):
                # check for global event keys
                allKeys = event.getKeys(['r','s','q'])
                if len(allKeys):
                    break
                if use_eyetracking:
                    eyetracker.draw_gazepoint(ctl_win)
                exp_win.flip()
                ctl_win.flip()
            else: # task completed
                break

            logging.flush()

            if not 'r' in allKeys:
                break
            print('restart')
        task.unload()
        if 'q' in allKeys:
            print('quit')
            break
        print('skip')

if __name__ == "__main__":
    lastLog = logging.LogFile("lastRun.log", level=logging.INFO, filemode='w')
    main()