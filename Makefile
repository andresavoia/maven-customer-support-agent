.PHONY: reset status web cli stop patterns

reset:
	./run.sh reset

status:
	./run.sh status

web:
	./run.sh web

cli:
	./run.sh cli

stop:
	./run.sh stop

patterns:
	.venv/bin/python -m guards.test_patterns
