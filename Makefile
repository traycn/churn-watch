.PHONY: setup test data eval eval-replay demo

setup:            ## install dependencies into .venv
	uv sync --extra dev

test:             ## unit tests (no API key needed)
	uv run pytest -q

data:             ## regenerate the synthetic export and test set
	uv run python data/generate.py --seed 7

eval:             ## score the test set for every model x prompt; needs ANTHROPIC_API_KEY
	uv run churnwatch eval

eval-replay:      ## reproduce the published scores from cached responses, no API key
	uv run churnwatch eval --replay

demo:             ## score the 200-account export and write out/report.html
	uv run churnwatch score data/accounts.csv --model claude-haiku-5-5 --prompt v2 --out out
