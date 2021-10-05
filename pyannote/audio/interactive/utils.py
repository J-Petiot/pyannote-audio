#!/usr/bin/env python
# encoding: utf-8

import base64
import io
import random
from typing import Dict, Iterator, List, Text

import numpy as np
import scipy.io.wavfile

from pyannote.core import Annotation, Segment, SlidingWindow

SAMPLE_RATE = 16000
Time = float


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
    # label
    return [
        {"start": segment.start - shift, "end": segment.end - shift, "label": "Speech"}
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
