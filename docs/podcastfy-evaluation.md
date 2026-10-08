# Podcastfy listening sample — 2026-10-08

Generated a Podcastfy sample using the existing Anthropic API key and Microsoft
Edge voices. The sample is available alongside excerpts from the approved Gemini
episode and the existing NotebookLM recording in the local Podcasts tab.

| Clip | Generation | Listen locally |
| --- | --- | --- |
| Podcastfy candidate | Podcastfy 0.4.3, Claude Sonnet 4.5 (`claude-sonnet-4-5-20250929`), Edge Andrew/Ava multilingual voices | [Sample](http://localhost:8585/podcast-file/2026-10-08-comparison-podcastfy-claude-edge) |
| Approved Gemini | Existing conversational Gemini Flash TTS recording, Puck/Aoede | [Excerpt](http://localhost:8585/podcast-file/2026-10-08-comparison-gemini-approved) |
| NotebookLM | Existing custom NotebookLM recording | [Excerpt](http://localhost:8585/podcast-file/2026-10-08-comparison-notebooklm-original) |

## Comparison method

- The candidate uses the cached custom episode source packet: seven articles,
  attendee notes, and the prior editorial briefing. Its newly generated script
  focuses on review evidence and evaluating agents on real work: 501 words,
  20 alternating speaker turns.
- Each clip is approximately 3:35. The references are opening excerpts from the
  existing recordings; their scripts differ. This compares the overall listening
  experience, not voice engines reading an identical transcript.
- All clips use two-pass loudness normalization targeting -16 LUFS and -1.5 dBTP,
  exported as mono 24 kHz / 128 kbps MP3. Reported output loudness is -16.07 LUFS
  for Podcastfy, -16.38 for Gemini, and -16.03 for NotebookLM. No tempo or pause
  edits were applied.
- Original recordings and the default Gemini provider remain unchanged. Every
  comparison entry retains the seven related article links.

## Integration findings

The Anthropic key works for Podcastfy's conversation generation. Edge requires
no additional API key, but it uses Microsoft's hosted speech service; this is
not a locally hosted open-source voice model.

Podcastfy was installed in `.scratch/podcastfy-sample/.venv`, without changing
Distill's dependencies. The experiment needed Playwright and Anthropic runtime
dependencies, plus FFmpeg/FFprobe. Podcastfy's legacy prompt loader is incompatible
with the installed LangSmith default restrictions: the sample loads the reviewed
upstream prompt at commit
`b2365f1166ffe61af8e08ef232276f87ee8f6d2a7ac13cc05a7769791969f1cd`
as local template text, without deserializing remote configuration. A generated
non-dialogue preamble was removed before synthesis; spoken dialogue was retained.

Local artifacts are under `output/podcastfy-comparison/`: the three clips, original
Podcastfy render, spoken transcript, and a manifest containing generation settings,
audio measurements, and file hashes. Each final MP3 decoded successfully with
FFmpeg. Naturalness and conversational delivery still require listener judgment.
