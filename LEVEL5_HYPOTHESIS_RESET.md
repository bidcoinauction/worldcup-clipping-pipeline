# Level 5 Hypothesis Reset

This note captures the highest-value unknowns for the GSMG.io Level 5 workstream. The goal is to reduce ambiguity before generating more mnemonic or private-key candidates.

## Current reset principle

Prioritize facts that collapse multiple branches of the search tree. Do not add more candidate-generation code until at least one of the ambiguity points below is answered or explicitly modeled.

## Highest-value ambiguities

### 1. Exact meaning of `XIXOIO`

This is the largest unresolved blocker. Treat `X`, `I`, and `O` as separate possible symbol classes until clarified:

- literal text from the mini-puzzle;
- operation symbols;
- geometry components;
- Roman-numeral-like values;
- binary-looking symbols or bit-shape encodings.

**Best clarification question:**

> In the Level 5 hint, are X, I, and O meant as operation symbols, geometry components, or just literal text from the mini-puzzle?

### 2. Meaning of the tiny line clues

Known tiny-line clue pairs:

- `40` with length `17`;
- `53` with length `6`.

Tested interpretations that have not produced a hit:

- value modifiers;
- order selectors;
- follow overrides.

The next model should test whether these clues are not about graph nodes `40` and `53` at all. Alternatives to model:

- coordinates;
- row or column selectors;
- minipuzzle decoding parameters;
- local geometry/topology annotations.

### 3. Intended rectangle metric for “area”

This single clarification would collapse a large portion of the current hypothesis tree. Candidate meanings:

- outer bounding-box area;
- inner rectangle area;
- shell or border area;
- actual white pixel count;
- another filled-rectangle metric defined by the puzzle image.

**Second-best clarification question:**

> When you say rectangle area, do you mean the filled outer bounding rectangle, the inner rectangle, or the white shell/border area?

### 4. Whether connectors are data nodes or only arrows

The current graph model treats `91` nodes as topology and `64` main rectangles as data. This assumption should be kept explicit.

- If connector geometry is data, the solver needs a different model.
- If connectors are only arrows, the current graph model remains plausible.

### 5. Where the `0x77` hint should appear

The Level 5 hint says byte `0x77` is part of the private key. This can be interpreted in at least two ways:

- literal: every correct 32-byte private-key candidate must contain byte `0x77`;
- intermediate: `0x77` appears before the final private-key derivation, so filtering final candidates by `0x77` may discard the correct path.

Until clarified, keep both filter modes available and label any results with the mode used.

## Recommended next actions

1. Ask the `XIXOIO` clarification question before further brute-force expansion.
2. Ask the rectangle-area clarification question if only one follow-up is possible.
3. Keep connector-as-data and connector-as-arrow as separate solver modes.
4. Track whether each candidate pipeline requires `0x77` in the final key or only in an intermediate stream.
