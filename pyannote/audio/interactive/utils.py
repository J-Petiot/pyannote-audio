#!/usr/bin/env python
# encoding: utf-8

# The MIT License (MIT)

# Copyright (c) 2020 CNRS

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# AUTHORS
# Hervé BREDIN - http://herve.niderb.fr

from typing import Text, List, Dict, Iterator, Tuple
from pyannote.core import Segment, SlidingWindow, Annotation, SlidingWindowFeature

import numpy as np
import random

import io
import base64
import scipy.io.wavfile

SAMPLE_RATE = 16000

Time = float

import warnings
import librosa
from librosa.util import valid_audio
from librosa.util.exceptions import ParameterError
from soundfile import SoundFile
import soundfile as sf
import torch

#Mettre waveform.T ?
def normalizeT(waveform: torch.Tensor) -> torch.Tensor:
    """Normalize waveform for better display in Prodigy UI"""
    return waveform / (waveform.abs().max() + 1e-8)


def normalize(waveform: np.ndarray) -> np.ndarray:
    """Normalize waveform for better display in Prodigy UI"""
    return waveform / (np.max(np.abs(waveform)) + 1e-8)


def to_base64(waveform: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Text:
    """Convert waveform to base64 data"""
    with io.BytesIO() as content:
        scipy.io.wavfile.write(content, sample_rate, waveform)
        content.seek(0)
        b64 = base64.b64encode(content.read()).decode()
        b64 = f"data:audio/x-wav;base64,{b64}"
    return b64


def to_audio_spans(annotation: Annotation, focus: Segment = None) -> Dict:
    """Convert pyannote.core.Annotation to Prodigy's audio_spans
    Parameters
    ----------
    annotation : Annotation
        Annotation with t=0s time origin.
    focus : Segment, optional
        When provided, use its start time as audio_spans time origin.
    Returns
    -------
    audio_spans : list of dict
    """
    shift = 0.0 if focus is None else focus.start
    #label
    return [
        {"start": segment.start - shift, "end": segment.end - shift, "label": 'Speech'}
        for segment, _, label in annotation.itertracks(yield_label=True)
    ]


def remove_audio_before_db(examples: List[Dict]) -> List[Dict]:
    """Remove (potentially heavy) 'audio' key from examples
    Parameters
    ----------
    examples : list of dict
        Examples.
    Returns
    -------
    examples : list of dict
        Examples with 'audio' key removed.
    """
    for eg in examples:
        if "audio" in eg:
            del eg["audio"]

    return examples


def chunks(
    duration: float, chunk: float = 30, shuffle: bool = False
) -> Iterator[Segment]:
    """Partition [0, duration] time range into smaller chunks
    Parameters
    ----------
    duration : float
        Total duration, in seconds.
    chunk : float, optional
        Chunk duration, in seconds. Defaults to 30.
    shuffle : bool, optional
        Yield chunks in random order. Defaults to chronological order.
    Yields
    ------
    focus : Segment
    """

    sliding_window = SlidingWindow(start=0.0, step=chunk, duration=chunk)
    whole = Segment(0, duration)

    if shuffle:
        chunks_ = list(chunks(duration, chunk=chunk, shuffle=False))
        random.shuffle(chunks_)
        for chunk in chunks_:
            yield chunk

    else:
        for window in sliding_window(whole):
            yield window
        if window.end < duration:
            yield Segment(window.end, duration)

def time2index(
    constraints_time: List[Tuple[Time, Time]], window: SlidingWindow,
) -> List[Tuple[int, int]]:
    """Convert time-based constraints to index-based constraints
    Parameters
    ----------
    constraints_time : list of (float, float)
        Time-based constraints
    window : SlidingWindow
        Window used for embedding extraction
    Returns
    -------
    constraints : list of (int, int)
        Index-based constraints
    """

    constraints = []
    for t1, t2 in constraints_time:
        i1 = window.closest_frame(t1)
        i2 = window.closest_frame(t2)
        if i1 == i2:
            continue
        constraints.append((i1, i2))
    return constraints


def index2index(
    constraints: List[Tuple[int, int]],
    keep: np.ndarray,
    reverse=False,
    return_mapping=False,
) -> List[Tuple[int, int]]:
    """Map constraints from original to keep-only index base
    Parameters
    ----------
    constraints : list of pairs
        Constraints in original index base.
    keep : np.ndarray
        Boolean array indicating whether to keep observations.
    reverse : bool
        Set to True to go from keep-only to original index base.
    return_mapping : bool, optional
        Return mapping instead of mapped constraints.
    Returns
    -------
    shifted_constraints : list of index pairs
        Constraints in keep-only index base.
    """

    if reverse:
        mapping = np.arange(len(keep))[keep]
    else:
        mapping = np.cumsum(keep) - 1

    if return_mapping:
        return mapping

    return [
        (mapping[i1], mapping[i2]) for i1, i2 in constraints if keep[i1] and keep[i2]
    ]





