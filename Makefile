PY ?= python
LL84 = $(PY) -m ll84

.PHONY: help install lint format test db-up db-down sample extract transform load review run-local \
        aws-setup glue-deploy glue-run athena-setup athena-queries run-aws aws-teardown clean

help:            ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install:         ## Install the package with Postgres + dev extras
	$(PY) -m pip install -e ".[postgres,dev]"

lint:            ## Ruff lint + format check
	ruff check src tests glue && ruff format --check src tests glue

format:          ## Auto-format
	ruff check --fix src tests glue && ruff format src tests glue

test:            ## Unit tests (set TEST_DATABASE_URL to include Postgres integration tests)
	$(PY) -m pytest

db-up:           ## Start local Postgres in Docker
	docker compose up -d --wait

db-down:         ## Stop local Postgres
	docker compose down

sample:          ## Generate synthetic LL84-shaped data (no internet needed)
	$(LL84) sample

extract:         ## Download the real LL84 dataset and land it in the raw zone
	$(LL84) extract

transform:       ## Raw -> curated parquet + EUI flags (locally, same code as Glue)
	$(LL84) transform

load:            ## Upsert curated tables + flags into Postgres
	$(LL84) load-postgres

review:          ## Show open EUI review items
	$(LL84) review

run-local:       ## extract -> transform -> load, all on this machine
	$(LL84) run-local

aws-setup:       ## Create S3 bucket, Glue role, Athena workgroup
	bash infra/setup_aws.sh

glue-deploy:     ## Build wheel, upload, create/update the Glue job
	bash infra/deploy_glue.sh

glue-run:        ## Run the Glue job and wait
	$(LL84) glue-run

athena-setup:    ## Create Athena database + external tables
	$(LL84) athena-setup

athena-queries:  ## Run every saved Athena query, save CSVs to reports/
	@for q in $$($(LL84) athena-query list 2>/dev/null | grep -v INFO); do \
		$(LL84) athena-query $$q --out reports/$$q.csv; done

run-aws: extract glue-run athena-setup load athena-queries  ## Full cloud pipeline

aws-teardown:    ## Delete all AWS resources (asks first)
	bash infra/teardown_aws.sh

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
