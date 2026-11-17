.PHONY: setup test packs budget eval eval-replay

setup:            ## install dependencies into .venv
	uv sync --extra dev

test:             ## unit tests (no API key needed)
	uv run pytest -q

packs:            ## list industry packs
	uv run churnwatch packs

budget:           ## spend so far and worst-case cost to finish; no API calls
	uv run churnwatch budget

eval:             ## score every pack's test set; needs ANTHROPIC_API_KEY (run in CI)
	uv run churnwatch eval

eval-replay:      ## reproduce every published score from cached responses, no API key
	uv run churnwatch eval --replay
