# toke-corpus

Corpus generation and validation pipeline for
[toke](https://github.com/karwalski/toke-spec).

Generates training data for toke language models using:
- Programmatic task curriculum generation
- LLM generation with local Qwen and Claude API fallback
- 4-language differential testing for correctness validation
- Local Qwen judge agent for quality review

## Requirements

- Python 3.11+
- Apple Silicon Mac with MLX and Qwen 2.5 Coder 32B (for local generation)
- Claude API key (for escalation)
- [tkc](https://github.com/karwalski/tkc) on PATH

## Setup

    pip install -e .
    cp .env.example .env   # add your Claude API key

## Running

    # Generate Phase A task curriculum
    python generator/curriculum.py --phase A --count 60000 --out tasks/

    # Run generation pipeline
    python pipeline/generate.py --tasks tasks/ --out corpus/ --phase A

    # Validate corpus schema
    python pipeline/validate_schema.py --corpus corpus/

## Corpus data

Generated corpus data is NOT stored in this repository.
Corpus files are stored locally on the project Mac Studio.

## Architecture

See `docs/pipeline-architecture.md`.

## Licence

Apache 2.0.
