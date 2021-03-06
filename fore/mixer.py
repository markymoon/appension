"""
Based off of `capsule`, by Tristan Jehan and Jason Sundram.
Heavily modified by Peter Sobot for integration with forever.fm.
Again by Mike iLL and Rosuav for Infinite Glitch
"""
import os
import gc
from . import apikeys
import logging
import pickle
import base64
import traceback
import threading
import subprocess
import multiprocessing
import weakref

from .lame import Lame
from .timer import Timer
from . import database
try: basestring
except NameError: basestring = (str, bytes)

from audiodata import AudioData

from .capsule_support import resample_features, \
	timbre_whiten, LOUDNESS_THRESH
	# removed: terminate, FADE_OUT, is_valid which we don't seem to be using.

from .transitions import managed_transition

log = logging.getLogger(__name__)

import sys
test = 'test' in sys.argv

import amen.echo_nest_converter

##########################################
## Code lifted from psobot's pyechonest ##
##########################################
import hashlib
import time

# import pyechonest.util
import numpy
# from echonest.remix.support.ffmpeg import ffmpeg

# Probe the system and find which name is available
ffmpeg_command = None
for command in ("ffmpeg", "avconv", "en-ffmpeg"):
	try:
		subprocess.Popen([command],stdout=subprocess.PIPE,stderr=subprocess.STDOUT).wait()
		ffmpeg_command = command
		break
	except OSError:
		# The command wasn't found. Move on to the next one.
		pass
if not ffmpeg_command:
	raise RuntimeError("No avconv/ffmpeg found, cannot continue")
log.info("Using %r for audio conversion.",ffmpeg_command)

class FFMPEGStreamHandler(threading.Thread):
	def __init__(self, infile, numChannels=2, sampleRate=44100):
		command = [ffmpeg_command]

		self.filename = None
		if isinstance(infile, basestring):
			self.filename = infile

		command.extend(["-i", self.filename or "pipe:0"])
		if numChannels is not None:
			command.extend(["-ac", str(numChannels)])
		if sampleRate is not None:
			command.extend(["-ar",str(sampleRate)])
		command.extend(["-f","s16le","-acodec","pcm_s16le","pipe:1"])
		log.info("Calling ffmpeg: %s", ' '.join(command)) # May be an imperfect representation of the command, but close enough

		# On Windows, psobot had this not closing FDs, despite doing so on other platforms. (????)
		# There's no os.uname() on Windows, presumably, and this is considered to be a reliable test.
		close_fds = hasattr(os, 'uname')
		self.p = subprocess.Popen(
			command,
			stdin=(subprocess.PIPE if not self.filename else None),
			stdout=subprocess.PIPE,
			stderr=open(os.devnull, 'w'),
			close_fds=close_fds
		)

		self.infile = infile if not self.filename else None
		if not self.filename:
			self.infile.seek(0)
			threading.Thread.__init__(self)
			self.daemon = True
			self.start()

	def __del__(self):
		if hasattr(self, 'p'):
			self.finish()

	def run(self):
		try:
			self.p.stdin.write(self.infile.read())
		except IOError:
			pass
		self.p.stdin.close()

	def finish(self):
		if self.filename:
			try:
				if self.p.stdin:
					self.p.stdin.close()
			except (OSError, IOError):
				pass
		try:
			self.p.stdout.close()
		except (OSError, IOError):
			pass
		try:
			self.p.kill()
		except (OSError, IOError):
			pass
		self.p.wait()

	#   TODO: Abstract me away from 44100Hz, 2ch 16 bit
	def read(self, samples=-1):
		if samples > 0:
			samples *= 2
		return [] # hacky hack
		arr = numpy.fromfile(self.p.stdout, dtype=numpy.int16, count=samples)
		if samples < 0 or len(arr) < samples:
			self.finish()
		arr = numpy.reshape(arr, (-1, 2))
		return arr

	def feed(self, samples):
		self.p.stdout.read(samples * 4)

class AudioStream(object):
	"""
	Very much like an AudioData, but vastly more memory efficient.
	However, AudioStream only supports sequential access - i.e.: one, un-seekable
	stream of PCM data directly being streamed from FFMPEG.
	"""

	def __init__(self, fobj):
		log.info("Audio Stream Init")
		print("Audio Stream Init")
		self.sampleRate = 44100
		self.numChannels = 2
		self.fobj = fobj
		self.stream = FFMPEGStreamHandler(self.fobj, self.numChannels, self.sampleRate)
		self.index = 0

	def __getitem__(self, index):
		"""
		Fetches a frame or slice. Returns an individual frame (if the index
		is a time offset float or an integer sample number) or a slice if
		the index is an `AudioQuantum` (or quacks like one). If the slice is
		"in the past" (i.e.: has been read already, or the current cursor is
		past the requested slice) then this will throw an exception.
		"""
		if isinstance(index, float):
			index = int(index * self.sampleRate)
		elif hasattr(index, "start") and hasattr(index, "duration"):
			index =  slice(float(index.start), index.start + index.duration)

		if isinstance(index, slice):
			if (hasattr(index.start, "start") and
				 hasattr(index.stop, "duration") and
				 hasattr(index.stop, "start")):
				index = slice(index.start.start, index.stop.start + index.stop.duration)

		if isinstance(index, slice):
			return self.getslice(index)
		else:
			return self.getsample(index)

	def getslice(self, index):
		"Help `__getitem__` return a new AudioData for a given slice"
		if isinstance(index.start, float):
			index = slice(int(index.start * self.sampleRate),
							int(index.stop * self.sampleRate), index.step)
		if index.start < self.index:
			self.stream.finish()
			self.stream = FFMPEGStreamHandler(self.fobj, self.numChannels, self.sampleRate)
			self.index = 0
		if index.start > self.index:
			self.stream.feed(index.start - self.index)
		self.index = index.stop

		return AudioData(None, self.stream.read(index.stop - index.start),
							sampleRate=self.sampleRate,
							numChannels=self.numChannels, defer=False)

	def getsample(self, index):
		#   TODO: Finish this properly
		raise NotImplementedError()
		if isinstance(index, float):
			index = int(index * self.sampleRate)
		if index >= self.index:
			self.stream.feed(index.start - self.index)
			self.index += index
		else:
			raise ValueError("Cannot seek backwards in AudioStream")

	def render(self):
		return self.stream.read()

	def finish(self):
		self.stream.finish()

	def __del__(self):
		if hasattr(self, "stream"):
			self.stream.finish()


class LocalAudioStream(AudioStream):
	"""
	Like a non-seekable LocalAudioFile with vastly better memory usage
	and performance. Takes a file-like object and supports slicing and
	rendering. Attempting to read from a part of the input file that
	has already been read will throw an exception.

	If analysis is provided, it is assumed to be a pickle of an
	AudioAnalysis, and will be used in preference to querying amen.
	"""
	def __init__(self, filename, analysis=None):
		AudioStream.__init__(self, filename)

		try:
			# Attempt to load up the existing analysis first.
			# Assume that a successful unpickling represents correct
			# data; there's no real guarantee of this, but if you
			# fiddle in the database, I won't stop you shooting
			# yourself in the foot.
			tempanalysis = pickle.loads(base64.b64decode(analysis))
		except (EOFError, TypeError, pickle.UnpicklingError):
			start = time.time()
			log.info("Loading audio...")
			audio = amen.audio.Audio(filename)
			log.info("Analyzing...")
			tempanalysis = amen.echo_nest_converter.AudioAnalysis(audio)
			log.info("Analyzed in %ss", time.time() - start)

		# By the time we get here, we ought to have a valid tempanalysis.
		# The very last attempt (passing the original initializer to
		# AudioAnalysis) will let any exceptions bubble all the way up,
		# so we don't have to deal with that here.
		self.analysis = tempanalysis
		# let's try adding this back in
		# on second thoughts, let's not
		# self.analysis.source = weakref.ref(self)

		class data(object):
			"""
			Massive hack - certain operations are intrusive and check
			`.data.ndim`, so in this case, we fake it.
			"""
			ndim = self.numChannels

		self.data = data
##############################################
## End code lifted from psobot's pyechonest ##
##############################################


def metadata_of(a):
	if hasattr(a, '_metadata'):
		return a._metadata.track_details
	if hasattr(a, 'track'):
		return metadata_of(a.track)
	if hasattr(a, 't1') and hasattr(a, 't2'):
		return (metadata_of(a.t1), metadata_of(a.t2))
	raise ValueError("No metadata found!")


def generate_metadata(a):
	d = {
		'action': a.__class__.__name__.split(".")[-1],
		'duration': a.duration,
		'samples': a.samples
	}
	m = metadata_of(a)
	if isinstance(m, tuple):
		m1, m2 = m
		log.info("HERE: %r", dir(m1))
		d['tracks'] = [{
			"metadata": m1,
			"start": a.s1,
			"end": a.e1
		}, {
			"metadata": m2,
			"start": a.s2,
			"end": a.e2
		}]
	else:
		d['tracks'] = [{
			"metadata": m,
			"start": a.start,
			"end": a.start + a.duration
		}]
	return d


class Mixer(multiprocessing.Process):
	def __init__(self, oqueue, infoqueue):
		self.infoqueue = infoqueue

		self.encoder = None
		self.oqueue = oqueue

		self.__track_lock = threading.Lock()
		self.__tracks = []

		self.transition_time = 30 if not test else 5
		self.__stop = False

		multiprocessing.Process.__init__(self)

	@property
	def tracks(self):
		self.__track_lock.acquire()
		tracks = self.__tracks
		self.__track_lock.release()
		return tracks

	@tracks.setter
	def tracks(self, new_val):
		self.__track_lock.acquire()
		self.__tracks = new_val
		self.__track_lock.release()

	@property
	def current_track(self):
		return self.tracks[0]

	def get_stream(self, x):
		for fname in (x.filename, "audio/"+x.filename):
			if os.path.isfile(fname):
				return fname
		# TODO: Fetch the contents from the database and save to fname
		raise NotImplementedError

	def analyze(self, x):
		if isinstance(x, list):
			return [self.analyze(y) for y in x]
		if isinstance(x, AudioData):
			return self.process(x)
		if isinstance(x, tuple):
			return self.analyze(*x)

		log.info("Grabbing stream [%r]...", x.id)
		saved = database.get_analysis(x.id)
		laf = LocalAudioStream(self.get_stream(x), saved)
		if not saved:
			database.save_analysis(x.id, base64.b64encode(pickle.dumps(laf.analysis,-1)).decode("ascii"))
		setattr(laf, "_metadata", x)
		return self.process(laf)

	def add_track(self, track):
		self.tracks.append(self.analyze(track))

	def process(self, track):
		# hacky hack
		#if not hasattr(track.analysis.pyechonest_track, "title"):
		#	setattr(track.analysis.pyechonest_track, "title", track._metadata.track_details['title'])
		log.info("Resampling features [%r]...", track._metadata.id)
		if len(track.analysis.beats):
			track.resampled = resample_features(track, rate='beats')
			track.resampled['matrix'] = timbre_whiten(track.resampled['matrix'])
		else:
			log.info("no beats returned for this track.")
			track.resampled = {"rate":'beats', "matrix": []}

		# hacky hack
		# track.gain = self.__db_2_volume(track.analysis.loudness)
		log.info("Done processing [%r].", track._metadata.id)
		return track

	def __db_2_volume(self, loudness):
		return (1.0 - LOUDNESS_THRESH * (LOUDNESS_THRESH - loudness) / 100.0)

	def generate_tracks(self):
		"""Yield a series of lists of track segments - helper for run()"""
		while len(self.tracks) < 2:
			log.info("Waiting for a new track.")
			track = database.get_track_to_play()
			try:
				self.add_track(track)
				log.info("Got a new track.")
			except Exception: # TODO: Why?
				log.error("Exception while trying to add new track:\n%s",
					traceback.format_exc())

		# Initial transition. 
		# yield initialize(self.tracks[0], self.tracks[1])

		mixer_state = {}

		while not self.__stop:
			while len(self.tracks) > 1:
				tra = managed_transition(self.tracks[0],
					self.tracks[1], mixer_state)
				del self.tracks[0].analysis
				gc.collect()
				yield tra
				log.debug("Finishing track 0 [%r]",self.tracks[0])
				from datetime import datetime
				now = datetime.now().time()
				self.tracks[0].finish()
				del self.tracks[0]
				gc.collect()
			if self.infoqueue is None: break # Hack: If we're not in infinite mode, don't wait for more tracks.
			log.info("Waiting for a new track.")
			try:
				self.add_track(database.get_track_to_play())
				log.info("Got a new track.")
			except ValueError:
				log.warning("Track too short! Trying another.")
			except Exception:
				log.error("Got an Exception while trying to add new track:\n%s",
					traceback.format_exc())

		log.error("Stopping!")
		# Last chunk. Should contain 1 instruction: fadeout.
		# CJA 20150227: Seems to be broken. Commenting this out may mean we ignore the
		# last track's transition info when building MajorGlitch.mp3, but this is not
		# serious. The track itself is correctly rendered; it will simply go on until
		# it reaches the end, and then stop, as per the King's advice.
		# yield terminate(self.tracks[-1], FADE_OUT)

	def run(self):
		database.reset_played()
		self.encoder = Lame(oqueue=self.oqueue)
		self.encoder.start()

		try:
			self.ctime = None
			for actions in self.generate_tracks():
				log.info("Rendering audio data for %d actions.", len(actions))
				for a in actions:
					try:
						with Timer() as t:
							#   TODO: Move the "multiple encoding" support into
							#   LAME itself - it should be able to multiplex the
							#   streams itself.
							self.encoder.add_pcm(a)
							if self.infoqueue: self.infoqueue.put(generate_metadata(a))
						log.info("Rendered in %fs!", t.ms)
					except Exception:
						log.error("Could not render %s. Skipping.\n%s SEE???", a,
								  traceback.format_exc())
				gc.collect()
		except Exception:
			log.error("Something failed in mixer.run:\n%s",
					  traceback.format_exc())
			self.stop()
			return

	def stop(self):
		self.__stop = True

	@property
	def stopped(self):
		return self.__stop

def build_entire_track(dest):
	"""Build the entire-track file, saving to dest"""
	with open(dest,"wb") as f:
		encoder = Lame(ofile=f)
		print("Building...")
		encoder.start()
		mixer = Mixer(None, None)
		for idx,track in enumerate(database.get_many_mp3(order_by="sequence,id")):
			print("Adding [%d]: ##%d %s (%r)"%(idx,track.id,track.track_details["artist"],track.filename))
			mixer.add_track(track)
		for actions in mixer.generate_tracks():
			print("Encoder: Got %d actions"%len(actions))
			for a in actions:
				print("Encoder: Adding %r"%(a,))
				encoder.add_pcm(a)
		encoder.finish()
		print("Build complete.")

def rebuild_major_glitch():
	build_entire_track("MajorGlitch.mp3")
	os.rename("MajorGlitch.mp3", "static/single-audio-files/MajorGlitch.mp3")

if __name__=="__main__":
	rebuild_major_glitch()
