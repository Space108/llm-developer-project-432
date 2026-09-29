.PHONY: temporal-worker index-fragments metrics metrics-all

temporal-worker:
	python -m app.temporal.worker

index-fragments:
	python -m app.commands.reindex

metrics:
	python -m app.commands.metrics

metrics-all:
	python -m app.commands.metrics --all
