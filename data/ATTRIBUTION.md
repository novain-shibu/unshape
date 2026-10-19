# Attribution for data/train_<lang>.txt

These files contain plain text taken from articles of the Wikipedia editions in Bengali, Gujarati, Hindi, Kannada,
Malayalam, Odia, Punjabi, Sinhala, Tamil and Telugu, written by **Wikipedia contributors**.

- Source: https://www.wikipedia.org/ (the respective language editions), taken from the Hugging Face dataset
  `wikimedia/wikipedia`, configuration `20231101.<lang>` (train split, rows 3000-3059 per language)
- Licence: Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0),
  https://creativecommons.org/licenses/by-sa/4.0/
- Changes: article text was extracted as plain text, concatenated and normalised; markup and non-text elements were removed.

These files are distributed under CC BY-SA 4.0 (not under the AGPL). Unshape uses them only to build a list of common
syllables and a character language model that break ties between candidate readings.
