# Live API checks — 19 September 2026

> Historical development notes. Referenced runs, screenshots, device calibration, and recordings are local artifacts and are not bundled with the public repository. Test counts and timings below describe those development sessions.

Subsequent live gameplay is documented in [live-gameplay.md](live-gameplay.md), including an autonomous 3–0 Training Camp win. The saved-screenshot experiments below are historical.

These were real authenticated requests, authorized after the initial offline implementation. They used saved gameplay screenshots and one manually constructed tactical state. They did not drive the game, measure capture latency, or demonstrate winning play.

## Results

| Provider/model | Attempts | Observation |
| --- | ---: | --- |
| Cerebras / Qwen 3.8 27B | 2 | Both returned HTTP 402, `payment_required`, with the message “Payment required to access this resource. Visit your billing tab.” No perception result. |
| Jev | 7 | All produced valid action distributions; observed request times approximately 0.34–1.08 seconds. |
| OpenAI / GPT-4.1 mini | 2 | Successful JSON perception; approximately 4.25–4.82 seconds per request. |
| OpenAI / GPT-4.1 | 2 | Successful JSON perception; approximately 3.80–4.24 seconds per request. |
| OpenAI / GPT-5.4 | 2 | Successful JSON perception; approximately 2.79–2.90 seconds per request. |

Total: 15 attempts, 13 successful API responses, zero game inputs. These are small samples, not throughput or percentile benchmarks. Exact response usage and timings are saved in the local run directories below. Dollar spending was not independently verified.

The controlled Jev scenario put an enemy Giant on the right with Mini P.E.K.K.A available. Jev selected `PLAY_0_mini_pekka_right_defense` in 884.7 ms. Its confidence was 0.33, below the current controller threshold of 0.35. That threshold has not been calibrated against gameplay success and was not lowered to make the test pass.

All six image-to-state-to-Jev probes completed. Combined perception/decision time was approximately 3.17–5.68 seconds, excluding capture. The production controller's 1.5-second state-age limit would reject these observations before allowing a move.

## Perception findings

- Tower-health text was useful in the reviewed samples, but this does not establish general OCR accuracy.
- GPT-4.1 mini labelled the red-bar enemy in the first screenshot as an ally. On the second screenshot it placed the enemy much closer to the river than the visible sprite.
- GPT-4.1 with explicit team-color instructions corrected the side in that sample, but gave inconsistent identities and counted tower decoration as deployed archers in another frame.
- GPT-5.4 with larger images still gave imprecise positions and an ambiguous enemy identity. Its complete pipeline was faster in these two requests, but still exceeded the current freshness limit.
- These comparisons changed model, prompt, and image resolution together. They do not isolate which change caused a difference, and high model confidence did not guarantee correct perception.

The current blocker is perception quality and latency. Increasing screenshot frequency alone will not fix either. The subsequent alternate-key test below establishes that Cerebras/Qwen is accessible, but it still has perception errors. Fixed tower coordinates should come from calibration rather than model estimates.

## Local evidence and reproduction

- `runs/live-api-probe/`: Cerebras errors and the controlled Jev scenario.
- `runs/live-api-openai-probe/`: GPT-4.1 mini plus Jev.
- `runs/live-api-openai-4.1-probe/`: GPT-4.1 plus Jev with extra team/coordinate instructions.
- `runs/live-api-openai-5.4-probe/`: GPT-5.4 plus Jev, 2048-pixel maximum image side and reasoning disabled.

Run folders are ignored by Git and remain local. No API keys or authorization headers are saved in these artifacts.

`scripts/probe_live.py` is the repeatable saved-screenshot test. It requires `--allow-api`, records the exact prompt and selection, and never connects to the device. The main runner remains Cerebras-based; OpenAI is an explicitly selected probe alternative.

References: [Cerebras billing requirements](https://support.cerebras.net/articles/5041581099-cerebras-self-serve-paygo-faq), [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision), [GPT-5.4 API model](https://developers.openai.com/api/docs/models/gpt-5.4).

## Follow-up: Gemma 4 31B

At the user's request, an authenticated model-catalog check returned only `qwen-3.8-27b` and `gpt-oss-120b`. A direct screenshot request with `gemma-4-31b` returned HTTP 404, code `model_archived`, type `model_archived_error`: “Model gemma-4-31b is archived and unavailable for the organization.” This is a model-availability failure, separate from the Qwen billing error.

Cerebras's [September 3, 2026 deprecation notice](https://inference-docs.cerebras.ai/support/deprecation#2026-09-03) confirms Gemma was removed from public endpoints and remains available on dedicated endpoints. No Gemma perception result or Jev decision was produced. This adds one failed inference attempt to the initial 15 above; the catalog GET is recorded separately.

Evidence: `runs/gemma-access-check/models.json` and `runs/live-api-cerebras-gemma-probe/error-details.json`, with the attempted configuration and request timing in the latter run directory.

## Follow-up: GPT nano models and alternate Cerebras key

The user requested lightweight GPT tests and then authorized trying `cerebras-2` from `.env`. These probes reused `captures/training-hand-02.png` and `captures/validation/frame-030.png`. Each completed perception was passed to Jev. No game inputs were sent.

| Model / configuration | Vision API times | Complete perception + Jev times | Result |
| --- | --- | --- | --- |
| GPT-5.4 nano, max side 2048, reasoning none | 4.65 s, 4.22 s | 6.76 s, 4.65 s | Two valid states, both followed by WAIT. |
| GPT-4.1 nano, max side 2048 | 15.43 s on first image | No completed pipeline | Returned 2,400 completion tokens, the configured limit; rejected as invalid or incomplete. Second image was not attempted in this run. |
| GPT-4.1 nano, max side 1024 | 3.45 s, 4.44 s | 4.42 s, 4.80 s | Two valid states; Jev chose WAIT, then an Arrows target. |
| Cerebras Qwen 3.8 27B, `cerebras-2`, max side 2048 | 1.65 s, 2.18 s | 2.79 s, 2.59 s | Two valid states; Jev chose WAIT, then an Arrows target. |

The alternate Cerebras key's authenticated model catalog returned HTTP 200 and listed `gpt-oss-120b` and `qwen-3.8-27b`. Both Qwen image requests then succeeded, so this key is usable for the intended vision model. The probe selected the alternate key only in process memory; it did not change `.env` or the main runner's default credential selection. Only the key's variable name is recorded.

Visual review against the original images found:

- GPT-5.4 nano missed the deployed enemy building in the first image and returned four purported troops around the princess towers. In the second image, it returned seven unknown units with misplaced coordinates instead of identifying the three visible allied minions and the enemy troop near the upper-left tower.
- GPT-4.1 nano at 1024 returned six invented unknown troops in the first image. In the second, it described knights, musketeers, cannons, and archers that did not match the visible troops, often placing several at exactly the same coordinates.
- Qwen detected an enemy object in the first image but left its type unknown and misplaced it. It also supplied enemy king HP despite no readable king HP number. In the second, it recognized three minions but marked them all as enemies despite their blue team indicators, added a second goblin, and misplaced the group.
- Both nano variants and Qwen read the four visible princess-tower HP values correctly in the completed samples. That narrow OCR success does not validate their overall battlefield state.

The nano variants did not improve latency or perception quality in these trials. Qwen with the alternate key was faster than the earlier successful GPT probes, but still exceeded the current 1.5-second state-age budget and misread tactically important details. There are only two completed frames per configuration; requests ran at different times, and the Cerebras prompt lacks the additional team/coordinate hints used in the OpenAI probe. This is not a controlled model ranking or a percentile benchmark.

This follow-up made 13 inference requests: six complete vision-to-Jev pipelines and one unusable GPT-4.1 nano response. Including earlier checks, there have been 29 inference attempts, with 12 complete screenshot pipelines, one controlled Jev-only scenario, one unusable nano response, and three Cerebras errors. Model-catalog GET requests are separate.

Evidence:

- `runs/live-api-openai-5.4-nano-probe/`
- `runs/live-api-openai-4.1-nano-probe/`
- `runs/live-api-openai-4.1-nano-1024-probe/`
- `runs/cerebras-2-access-check/models.json`
- `runs/live-api-cerebras-2-probe/`

Model selection was checked against official OpenAI documentation for [GPT-5.4 nano](https://developers.openai.com/api/docs/models/gpt-5.4-nano) and [GPT-4.1 nano](https://developers.openai.com/api/docs/models/gpt-4.1-nano), both of which support image input and structured output.

## Other providers considered

- [Groq vision](https://console.groq.com/docs/vision) lists `qwen/qwen3.8-27b` with image input and JSON mode. It is the closest alternative to the intended Cerebras model. Advertised output speed does not establish complete screenshot latency; it has not been tested here.
- [Together AI](https://www.together.ai/models/gemma-4-31b) lists `google/gemma-4-31B-it` with image input and serverless availability, providing a route for the requested Gemma model. It has not been tested with this project or an account key.
- [Google's model catalog](https://ai.google.dev/gemini-api/docs/models) lists Gemini Flash and Flash-Lite options. These remain untested alternatives.

Since `cerebras-2` works, changing providers is optional. The next useful experiment is improving Qwen's extraction of teams and positions on these same images, with static tower geometry supplied by calibration. A custom detector trained on labelled gameplay is another route if general vision models remain inaccurate.
