#!/usr/bin/env python
# encoding: utf=8

"""
audition.py

Functions to combine 'Basic' and 'Overdub' tracks from 'Glitch Recording Studio'.

Created by Mike iLL with mentorship of Rosuav.

track1 = LocalAudioFile('acapella/Mike_iLL_a_capella.mp3')
track2 = LocalAudioFile('static/instrumentals/dgacousticlikMP3.mp3')
ct.render_track('Mike_iLL_a_capella.mp3', 'dgacousticlikMP3.mp3', itrim=8.5)
"""
from __future__ import print_function
try: import echonest.remix.audio as audio
except ImportError: pass # If not available, render_track will fail
import logging
from .action import Playback_static as pb
from .action import Fadeout_static as fo
from .action import audition_render, remove_channel, left_right_merge

log = logging.getLogger(__name__)

LOUDNESS_THRESH = -8

def combine_tracks(track1, track2, remove=0):
    #trim beginning of track1
    track1.data = track1.data[14000:]
    if remove == 'right':
        track2 = remove_channel(track2, remove="right")
    return left_right_merge(track1, track2)


def format_track(track, itrim=0.0, otrim=0.0, fadeout=5):
    playback = pb(track, itrim, track.analysis.duration - otrim)
    fade = fo(track, track.analysis.duration - otrim, fadeout)
    return [playback, fade]
    
def render_track(file1, file2, itrim=0.0, fadeout=5, remove=0):
    filename = file1
    track2 = audio.LocalAudioFile('static/instrumentals/'+file2)
    print('###########')
    print('acapella/'+file1)
    print('###########')
    track1 = audio.LocalAudioFile('acapella/'+file1)
    otrim = max(track1.analysis.duration, track2.analysis.duration) - min(track1.analysis.duration, track2.analysis.duration)
    together = combine_tracks(track1, track2, remove=remove)
    formatted = format_track(together, itrim=itrim, otrim=otrim, fadeout=fadeout)
    render(formatted, 'audition_audio/'+filename)
