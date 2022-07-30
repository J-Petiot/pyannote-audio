# The MIT License (MIT)
#
# Copyright (c) 2021-2022 CNRS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# AUTHORS
# Jim Petiot
# Hervé Bredin

import random
from copy import deepcopy
from functools import cmp_to_key
from itertools import groupby
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np
import torch
from pyannote.core import Annotation, Segment
from pyannote.core.utils.generators import string_generator
from scipy.spatial.distance import cdist

from pyannote.audio import Audio, Inference, Pipeline

from ..common.utils import (
    AudioForProdigy,
    get_audio_spans,
    get_chunks,
    remove_context,
    to_audio_spans,
)


class RecipeHelper:
    def __init__(self, chunk, speakers_dir, embedding="pyannote/embedding"):
        self.inference = Inference(embedding, window="whole")
        dim = self.inference.model.introspection.dimension
        self.speakers = np.array(
            [],
            dtype=[
                ("name", "U100"),
                ("embedding", "f4", dim),
                ("nb", "i4"),
                ("excerpt", "U1000000"),
            ],
        )
        self.chunk = chunk
        self.speakers_dir = speakers_dir
        self.buffer = {}
        self.audiobuffer = {}
        self.generator = string_generator()
        self.validate_generator = []
        self.context = 1

    def on_exit(self, controller):
        np.save(self.speakers_dir / "speakers.npy", self.speakers)

    def get_audio(self, annotation, label, path, focus):
        combine_waveform = torch.Tensor([[]])
        audio_for_pipeline = Audio(mono=True)
        overlap = annotation.get_overlap()
        annotation = annotation.extrude(overlap)

        for segment in annotation.label_timeline(label):
            waveform, sample_rate = audio_for_pipeline.crop(
                path, Segment(segment.start + focus, segment.end + focus), mode="pad"
            )
            combine_waveform = torch.cat((combine_waveform, waveform), dim=1)

        return combine_waveform

    def update(self, answers):
        for eg in answers:

            if eg["answer"] != "accept":
                return

            audio_spans = eg["audio_spans"]
            annotation = remove_context(eg)
            audio_spans = to_audio_spans(annotation)

            audio_spans = sorted(audio_spans, key=lambda x: x["label"])

            for label, segments in groupby(audio_spans, key=lambda x: x["label"]):
                if (label in eg) and (eg[label] != ""):
                    speaker = eg[label]
                else:
                    if label.startswith("SPEAKER_"):
                        speaker = self.validate_generator.pop()
                    else:
                        speaker = label

                speaker = speaker.strip()

                combine_waveform = torch.Tensor([])
                audio_for_pipeline = Audio(mono=True)
                audio_for_prodigy = AudioForProdigy()
                duration = 0

                for segment in list(segments):
                    file_segment = Segment(
                        segment["start"] + eg["chunk"]["start"],
                        segment["end"] + eg["chunk"]["start"],
                    )
                    waveform, sample_rate = audio_for_pipeline.crop(
                        eg["path"], file_segment, mode="pad"
                    )
                    combine_waveform = torch.cat((combine_waveform, waveform), dim=1)
                    duration += file_segment.duration

                i = np.where(self.speakers["name"] == speaker)
                i = i[0][0]
                combine_waveform = torch.cat(
                    (combine_waveform, self.buffer[speaker][0]), dim=1
                )

                audio_waveform = self.get_audio(
                    annotation, label, eg["path"], eg["chunk"]["start"]
                )
                audio_waveform = torch.cat(
                    (audio_waveform, self.audiobuffer[speaker]), dim=1
                )

                if (len(audio_waveform[0]) / sample_rate) > 5:
                    split_audio = torch.split(audio_waveform, 5 * sample_rate, dim=1)
                    audio_waveform = split_audio[0]
                if len(audio_waveform[0]) > 0:
                    audio = audio_for_prodigy.to_base64(audio_waveform, sample_rate)
                else:
                    audio = "data:audio/x-wav;base64,"

                self.audiobuffer[speaker] = audio_waveform

                if (self.speakers[i]["nb"] > 0) and (
                    self.buffer[speaker][1] + duration >= 5
                ):
                    empty_waveform = torch.Tensor([])
                    self.buffer[speaker] = [empty_waveform, 0]

                    embedding = self.get_embedding(combine_waveform, sample_rate)

                    self.speakers[i]["embedding"] = (
                        (self.speakers[i]["nb"] * self.speakers[i]["embedding"])
                        + embedding
                    ) / (self.speakers[i]["nb"] + 1)
                    self.speakers[i]["nb"] += 1
                    self.speakers[i]["excerpt"] = audio

                else:
                    duration = self.buffer[speaker][1] + duration
                    self.buffer[speaker] = [combine_waveform, duration]
                    self.speakers[i]["excerpt"] = audio
                    if self.speakers[i]["nb"] == 0:
                        self.speakers[i]["nb"] = 1

    def validate_answer(self, eg):
        if eg["answer"] != "accept":
            return

        audio_spans = eg["audio_spans"]
        annotation = remove_context(eg)
        audio_spans = to_audio_spans(annotation)

        audio_spans = sorted(audio_spans, key=lambda x: x["label"])

        for label, segments in groupby(audio_spans, key=lambda x: x["label"]):
            if (label in eg) and (eg[label] != ""):
                speaker = eg[label]
            else:
                if label.startswith("SPEAKER_"):
                    speaker = next(self.generator)
                    self.validate_generator.insert(0, speaker)
                else:
                    speaker = label

            if speaker.isspace():
                assert False, "You can't name a speaker with an empty name."

            speaker = speaker.strip()

            if speaker not in self.buffer:
                empty_waveform = torch.Tensor([])
                self.buffer[speaker] = [empty_waveform, 0]
                self.audiobuffer[speaker] = empty_waveform
                combine_waveform = torch.Tensor([])
                audio_for_pipeline = Audio(mono=True)

                for segment in list(segments):
                    file_segment = Segment(
                        segment["start"] + eg["chunk"]["start"],
                        segment["end"] + eg["chunk"]["start"],
                    )
                    waveform, sample_rate = audio_for_pipeline.crop(
                        eg["path"], file_segment, mode="pad"
                    )
                    combine_waveform = torch.cat((combine_waveform, waveform), dim=1)

                embedding = self.get_embedding(combine_waveform, sample_rate)
                audio_waveform = self.get_audio(
                    annotation, label, eg["path"], eg["chunk"]["start"]
                )
                if len(audio_waveform[0]) > 0:
                    audio_for_prodigy = AudioForProdigy()
                    audio = audio_for_prodigy.to_base64(audio_waveform, sample_rate)
                else:
                    audio = "data:audio/x-wav;base64,"
                size = self.speakers.size + 1
                self.speakers.resize(size, refcheck=False)
                self.speakers[self.speakers.size - 1] = (speaker, embedding, 0, audio)

    def get_embedding(self, wav, sample_rate):
        try:
            embedding = self.inference({"waveform": wav, "sample_rate": sample_rate})
        except (RuntimeError, ValueError):
            embedding = [float("nan")]
        return embedding

    def compareOverlap(self, region1, region2):
        s1 = Segment(region1["start"], region1["end"])
        s2 = Segment(region2["start"], region2["end"])
        if s1 in s2:
            return 1
        elif s2 in s1:
            return -1
        else:
            return 0

    def stream(
        self,
        pipeline: Pipeline,
        source: Path,
        labels: List[str],
        randomize: bool = False,
    ) -> Iterable[Dict]:
        """Stream for `pyannote.audio` recipe

        Applies pretrained pipeline and sends the results for manual correction

        Parameters
        ----------
        pipeline : Pipeline
            Pretrained pipeline.
        source : Path
            Directory containing audio files to process.
        labels : list of string
            List of expected pipeline labels.
        chunk : float, optional
            Duration of chunks, in seconds. Defaults to 30s.

        Yields
        ------
        task : dict
            Prodigy task with the following keys:
            "path" : path to audio file
            "chunk" : chunk start and end times
            "audio" : base64 encoding of audio chunk
            "text" : chunk identifier "{filename} [{start} {end}]"
            "audio_spans" : list of audio spans {"start": ..., "end": ..., "label": ...}
            "audio_spans_original" : deep copy of "audio_spans"
            "meta" : metadata displayed in Prodigy UI {"file": ..., "start": ..., "end": ...}
            "config": {"labels": list of labels}
        """
        if len(self.speakers) > 0:
            empty_waveform = torch.Tensor([])
            for speaker in self.speakers["name"]:
                self.buffer[speaker] = [empty_waveform, 0]
                self.audiobuffer[speaker] = empty_waveform

        context = getattr(pipeline, "context", self.context)
        chunk = self.chunk

        audio_for_prodigy = AudioForProdigy()
        audio_for_pipeline = Audio(mono=True)

        chunks = get_chunks(source, chunk_duration=chunk)
        if randomize:
            chunks = list(chunks)
            random.shuffle(chunks)

        for file, excerpt in chunks:
            path = file["path"]
            filename = file["text"]
            text = f"{filename} [{excerpt.start:.1f} - {excerpt.end:.1f}]"

            # load contextualized audio excerpt
            duration = audio_for_pipeline.get_duration(file["path"])
            start = (
                (excerpt.start - context)
                if (excerpt.start > self.context)
                else excerpt.start
            )
            end = (
                (excerpt.end + context)
                if ((excerpt.end + context) < duration)
                else excerpt.end
            )
            excerpt_with_context = Segment(start=start, end=end)
            waveform_with_context, sample_rate = audio_for_pipeline.crop(
                path, excerpt_with_context, mode="pad"
            )

            # run pipeline on contextualized audio excerpt
            output: Annotation = pipeline(
                {"waveform": waveform_with_context, "sample_rate": sample_rate}
            )

            # crop, shift, and format output for visualization in Prodigy
            audio_spans = get_audio_spans(output, excerpt_with_context)

            # load audio excerpt for visualization in Prodigy
            audio = audio_for_prodigy.crop(path, excerpt_with_context)

            additionnal_labels = sorted(set(labels) | set(output.labels()))

            # group by label
            audio_spans = sorted(audio_spans, key=lambda x: x["label"])

            for label, segments in groupby(audio_spans, key=lambda x: x["label"]):

                combine_waveform = torch.Tensor([])
                for segment in list(segments):
                    waveform, sample_rate = audio_for_pipeline.crop(
                        path,
                        Segment(
                            segment["start"] + excerpt_with_context.start,
                            segment["end"] + excerpt_with_context.start,
                        ),
                    )
                    combine_waveform = torch.cat((combine_waveform, waveform), dim=1)

                embedding = self.get_embedding(combine_waveform, sample_rate)

                if not np.isnan(embedding).any():
                    try:
                        distances = cdist(
                            self.speakers["embedding"],
                            embedding.reshape(1, -1),
                            metric="cosine",
                        )
                        index_min = np.argmin(distances)

                    except ValueError:
                        distances = [1000]
                        index_min = 0

                    # TODO update thresold according to the result
                    if distances[index_min] < 0.5:
                        genlabel = self.speakers[index_min]["name"]
                        for span in audio_spans:
                            if span["label"] == label:
                                span.update({"label": genlabel})

            blocks = [{"view_id": "audio_manual"}]
            all_labels = list(self.speakers["name"]) + additionnal_labels

            sounds = {spk[0]: spk[3] for spk in self.speakers}
            # sort regions that are inside other regions to avoid unclickable issues
            audio_spans = sorted(audio_spans, key=cmp_to_key(self.compareOverlap))

            for label in additionnal_labels:
                blocks.append(
                    {
                        "view_id": "text_input",
                        "field_id": label,
                        "field_label": label,
                        "field_rows": 1,
                        "field_placeholder": "Choose a name for the rest of the recording",
                        "field_suggestions": list(self.speakers["name"]),
                    }
                )
            yield {
                "path": str(path),
                "text": text,
                "audio": audio,
                "sounds": sounds,
                "audio_spans": audio_spans,
                "audio_spans_original": deepcopy(audio_spans),
                "chunk": {"start": excerpt.start, "end": excerpt.end},
                "chunk_crop": {
                    "start": excerpt.start - start,
                    "end": excerpt.start - start + chunk,
                },
                "config": {"labels": all_labels, "blocks": blocks},
                "meta": {
                    "file": filename,
                    "start": f"{excerpt.start:.1f}",
                    "end": f"{excerpt.end:.1f}",
                },
            }