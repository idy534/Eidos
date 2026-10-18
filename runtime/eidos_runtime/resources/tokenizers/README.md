# Offline reference vocabulary

Eidos uses `tiktoken==0.13.0` with the upstream `cl100k_base` vocabulary as a
reference estimator. It is not an exact tokenizer for every configured model,
and it does not include a provider's private message framing. Provider input
usage and snapshot-bound calibration remain separate from this estimate.

Source: https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken

SHA-256: `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`

The vocabulary and encoding configuration come from OpenAI's tiktoken project
(MIT license). The runtime only opens the bundled local vocabulary path. It
never resolves an encoding through a model name or downloads vocabulary data.
