"""Generating or processing video, often using ffmpeg"""
import numpy as np
import subprocess
import re
import datetime

class OutOfFrames(BaseException):
    """Exception raised when more frames cannot be extracted from a video"""
    pass

def get_frame(filename, frametime, pix_fmt='gray', bufsize=10**9):
    """Returns a single frame from a video as an array.
    
    This creates an ffmpeg process and extracts data from it with a pipe.
    Not tested on pix_fmt other than 'gray'
    
    This syntax is used to seek with ffmpeg:
        ffmpeg -ss %frametime% -i %filename% -vframes 1 ...
    This is supposed to be relatively fast while still accurate.
    
    TODO: Get this to return multiple frames from the same instance
    
    Returns:
        frame, stdout, stderr
        frame : 2d array, of shape (height, width)
        stdout : typically blank
        stderr : ffmpeg's text output
    """
    v_width, v_height = get_video_aspect(filename)
    
    # Create the command
    command = ['ffmpeg', 
        '-ss', str(frametime),
        '-i', filename,
        '-vframes', '1',       
        '-f', 'image2pipe',
        '-pix_fmt', pix_fmt,
        '-vcodec', 'rawvideo', '-']
    
    # To store result
    res_l = []
    frames_read = 0

    # Init the pipe
    # We set stderr to PIPE to keep it from writing to screen
    # Do this outside the try, because errors here won't init the pipe anyway
    pipe = subprocess.Popen(command, 
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
        bufsize=bufsize)

    try:
        read_size = v_width*v_height
        raw_image = pipe.stdout.read(read_size)    
        if len(raw_image) < read_size:
            raise OutOfFrames        
        flattened_im = np.fromstring(raw_image, dtype='uint8')
        frame = flattened_im.reshape((v_height, v_width))    
    
    except OutOfFrames:
        print "warning: cannot get frame"
        frame = None
    
    finally:
        # Restore stdout
        pipe.terminate()

        # Keep the leftover data and the error signal (ffmpeg output)
        stdout, stderr = pipe.communicate()    
    
    return frame, stdout, stderr


def frame_dump(filename, frametime, output_filename='out.png', 
    meth='ffmpeg fast', subseek_cushion=20., verbose=False, dry_run=False,
    very_verbose=False):
    """Dump the frame in the specified file.
    
    Probably better to use get_frame instead.
    
    If the subprocess fails, CalledProcessError is raised.
    Special case: if seek is beyond the end of the file, nothing is done
    and no error is raised
    (because ffmpeg does not report any problem in this case).
    
    Values for meth:
        'ffmpeg best' : Seek quickly, then accurately
            ffmpeg -y -ss :coarse: -i :filename: -ss :fine: -vframes 1 \
                :output_filename:
        'ffmpeg fast' : Seek quickly
            ffmpeg -y -ss :frametime: -i :filename: -vframes 1 :output_filename:
        'ffmpeg accurate' : Seek accurately, but takes forever
            ffmpeg -y -i :filename: -ss frametime -vframes 1 :output_filename:
        'mplayer' : This takes forever and also dumps two frames, the first 
            and the desired. Not currently working but something like this:
            mplayer -nosound -benchmark -vf framestep=:framenum: \
                -frames 2 -vo png :filename:
    
    Note that output files are always overwritten without asking.
    
    With recent, non-avconv versions of ffmpeg, it appears that 'ffmpeg fast'
    is just as accurate as 'ffmpeg best', and is now the preferred method.
    
    Use scipy.misc.imread to read them back in.
    
    Source
        https://trac.ffmpeg.org/wiki/Seeking%20with%20FFmpeg
    """
    
    if meth == 'mplayer':
        raise ValueError, "mplayer not supported"
    elif meth == 'ffmpeg best':
        # Break the seek into a coarse and a fine
        coarse = np.max([0, frametime - subseek_cushion])
        fine = frametime - coarse
        syscall = 'ffmpeg -y -ss %r -i %s -ss %r -vframes 1 %s' % (
            coarse, filename, fine, output_filename)
    elif meth == 'ffmpeg accurate':
        syscall = 'ffmpeg -y -i %s -ss %r -vframes 1 %s' % (
            filename, frametime, output_filename)
    elif meth == 'ffmpeg fast':
        syscall = 'ffmpeg -y -ss %r -i %s -vframes 1 %s' % (
            frametime, filename, output_filename)
    
    if verbose:
        print syscall
    if not dry_run:
        #os.system(syscall)
        syscall_l = syscall.split(' ')
        syscall_result = subprocess.check_output(syscall_l, 
            stderr=subprocess.STDOUT)
        if very_verbose:
            print syscall_result

def process_chunks_of_video(filename, n_frames, func='mean', verbose=False,
    frame_chunk_sz=1000, bufsize=10**9,
    image_w=None, image_h=None, pix_fmt='gray',
    finalize='concatenate'):
    """Read frames from video, apply function, return result
    
    Uses a pipe to ffmpeg to load chunks of frame_chunk_sz frames, applies
    func, then stores just the result of func to save memory.
    
    If n_frames > # available, returns just the available frames with a
    warning.
    
    filename : file to read
    n_frames : number of frames to process
    func : function to apply to each frame
        If 'mean', then func = lambda frame: frame.mean()
        If 'keep', then func = lambda frame: frame
        'keep' will return every frame, which will obviously require a lot
        of memory.
    verbose : If True, prints out frame number for every chunk
    frame_chunk_sz : number of frames to load at once from ffmpeg
    bufsize : sent to subprocess.Popen
    image_w, image_h : width and height of video in pxels
    pix_fmt : Sent to ffmpeg
    
    TODO: 
    if n_frames is None, set to max or inf
    get video params using ffprobe
    """
    # Default function is mean luminance
    if func == 'mean':
        func = lambda frame: frame.mean()
    elif func == 'keep':
        func = lambda frame: frame
    elif func is None:
        raise ValueError("must specify frame function")
    
    # Get aspect
    if image_w is None:
        image_w, image_h = get_video_aspect(filename)
    
    # Create the command
    command = ['ffmpeg', 
        '-i', filename,
        '-f', 'image2pipe',
        '-pix_fmt', pix_fmt,
        '-vcodec', 'rawvideo', '-']
    
    # To store result
    res_l = []
    frames_read = 0

    # Init the pipe
    # We set stderr to PIPE to keep it from writing to screen
    # Do this outside the try, because errors here won't init the pipe anyway
    pipe = subprocess.Popen(command, 
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, 
        bufsize=bufsize)

    # Catch any IO errors and restore stdout
    try:
        # Read in chunks
        out_of_frames = False
        while frames_read < n_frames and not out_of_frames:
            if verbose:
                print frames_read
            # Figure out how much to acquire
            if frames_read + frame_chunk_sz > n_frames:
                this_chunk = n_frames - frames_read
            else:
                this_chunk = frame_chunk_sz
            
            # Read this_chunk, or as much as we can
            raw_image = pipe.stdout.read(image_w*image_h*this_chunk)
            
            # check if we ran out of frames
            if len(raw_image) < image_w * image_h * this_chunk:
                print "warning: ran out of frames"
                out_of_frames = True
                this_chunk = len(raw_image) / image_w / image_h
                assert this_chunk * image_w * image_h == len(raw_image)
            
            # Process
            flattened_im = np.fromstring(raw_image, dtype='uint8')
            video = flattened_im.reshape((this_chunk, image_h, image_w))
            
            # Store as list to avoid dtype and shape problems later
            #chunk_res = np.asarray(map(func, video))
            chunk_res = map(func, video)
            
            # Store
            res_l.append(chunk_res)
            
            # Update
            frames_read += this_chunk

    except:
        raise

    finally:
        # Restore stdout
        pipe.terminate()

        # Keep the leftover data and the error signal (ffmpeg output)
        stdout, stderr = pipe.communicate()

    # Stick chunks together
    if len(res_l) == 0:
        print "warning: no data found"
        res = np.array([])
    elif finalize == 'concatenate':
        res = np.concatenate(res_l)
    elif finalize == 'listcomp':
        res = np.array([item for sublist in res_l for item in sublist])
    elif finalize == 'list':
        res = res_l
    else:
        print "warning: unknown finalize %r" % finalize
        res = res_l
        
    return res

def get_video_aspect(video_filename):
    """Returns width, height of video using ffprobe"""
    # Video duration and hence start time
    proc = subprocess.Popen(['ffprobe', video_filename],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    res = proc.communicate()[0]

    # Check if ffprobe failed, probably on a bad file
    if 'Invalid data found when processing input' in res:
        raise ValueError("Invalid data found by ffprobe in %s" % video_filename)
    
    # Find the video stream
    width_height_l = []
    for line in res.split("\n"):
        # Skip lines that aren't stream info
        if not line.strip().startswith("Stream #"):
            continue
        
        # Check that this is a video stream
        comma_split = line.split(',')
        if " Video: " not in comma_split[0]:
            continue
        
        # The third group should contain the size and aspect ratio
        if len(comma_split) < 3:
            raise ValueError("malform video stream string:", line)
        
        # The third group should contain the size and aspect, separated
        # by spaces
        size_and_aspect = comma_split[2].split()        
        if len(size_and_aspect) == 0:
            raise ValueError("malformed size/aspect:", comma_split[2])
        size_string = size_and_aspect[0]
        
        # The size should be two numbers separated by x
        width_height = size_string.split('x')
        if len(width_height) != 2:
            raise ValueError("malformed size string:", size_string)
        
        # Cast to int
        width_height_l.append(map(int, width_height))
    
    if len(width_height_l) > 1:
        print "warning: multiple video streams found, returning first"
    return width_height_l[0]


def get_video_duration(video_filename, return_as_timedelta=False):
    """Return duration of video using ffprobe"""
    # Video duration and hence start time
    proc = subprocess.Popen(['ffprobe', video_filename],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    res = proc.communicate()[0]

    # Check if ffprobe failed, probably on a bad file
    if 'Invalid data found when processing input' in res:
        raise ValueError(
            "Invalid data found by ffprobe in %s" % video_filename)

    # Parse out start time
    duration_match = re.search("Duration: (\S+),", res)
    assert duration_match is not None and len(duration_match.groups()) == 1
    video_duration_temp = datetime.datetime.strptime(
        duration_match.groups()[0], '%H:%M:%S.%f')
    video_duration = datetime.timedelta(
        hours=video_duration_temp.hour, 
        minutes=video_duration_temp.minute, 
        seconds=video_duration_temp.second,
        microseconds=video_duration_temp.microsecond)    
    
    if return_as_timedelta:
        return video_duration
    else:
        return video_duration.total_seconds()