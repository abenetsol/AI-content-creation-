# One-command film. On Windows use `py -m pipeline.film` or install make (winget install ezwinports.make).
PY ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,$(if $(wildcard .venv/bin/python),.venv/bin/python,python3))

.PHONY: setup validate film film-nogpu radio narration keyframes refs shots music assemble draft costs clean-film

setup:            ## create venv and install dependencies
	python -m venv .venv && $(PY) -m pip install -r requirements.txt

validate:         ## validate storyboard.json against schema/
	$(PY) -c "from pipeline.common import load_storyboard; s=load_storyboard(); print('valid:', len(s['shots']), 'shots')"

film:             ## the whole pipeline, both aspect ratios
	$(PY) -m pipeline.film

film-nogpu:       ## whole pipeline without Kaggle (Ken Burns + cached/placeholder music)
	$(PY) -m pipeline.film --skip-gpu

radio:            ## narration + placeholder music on black
	$(PY) -m pipeline.film --radio

narration: ; $(PY) -m pipeline.narration
refs:      ; $(PY) -m pipeline.keyframes --refs
keyframes: ; $(PY) -m pipeline.keyframes
shots:     ; $(PY) -m pipeline.shots
music:     ; $(PY) -m pipeline.music
assemble:  ; $(PY) -m pipeline.assemble
draft:     ; $(PY) -m pipeline.assemble --aspect 9x16 --draft
costs:     ; $(PY) -m pipeline.costs

clean-film:       ## remove rendered films (keeps the model-output cache)
	rm -rf outputs/film outputs/assemble
