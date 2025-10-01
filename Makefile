AWS_REGION=us-east-1

install-deps:
	cd lmbd_event_listener && npm install
	cd lmbd_message_evaluator && pip install -r requirements.txt
	cd lmbd_slack_mcp_agent && pip install -r requirements.txt

build:
	sam build

deploy:
	sam build --debug
	sam deploy --guided \
		--no-confirm-changeset \
        --capabilities CAPABILITY_IAM \
		--resolve-image-repos

fd:
	sam deploy

drop_sam:
	sam delete

watch_docker:
	watch -n 1 docker ps -a

test-agent-local: build
	@echo "\nTesting Python Agent with many messages or threads..."
	> logs/output.log
	> logs/test-agent.log
	sam local invoke SlackAgentFunction -e events/test-bedrock_keys.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-open-search.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-claude_code-cursor.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-genai-processors.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-lambda-mcp-serverless.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-nova-sonic.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-tool-aws-serverless-mcp_1.json --debug --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-rag-projects.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-cdk-drift.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-waf-ddos.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log
	sam local invoke SlackAgentFunction -e events/test-cost-comparison-feature.json --env-vars ../env.json 2>&1 | tee -a logs/test-agent.log | tail -n 1 >> logs/output.log

test-arch-agent: build
	clear
	@echo "\nTesting Architecture Agent with 1st message emulating WhatsApp request..."
	> logs/test-architecture-agent.log
	bash -lc 'sam local invoke ArchitectureAgentFunction -e events/architecture_agent/test-wsp.json --env-vars ../env.json 2>&1 | tee -a logs/test-architecture-agent.log >(tail -n 1 | jq -C .)'

enter-arch-agent-docker:
	docker build -t arch-agent-debug lmbd_architecture_mcp_agent/.
	docker run --rm -it --name arch-agent-debug-c --entrypoint /bin/sh arch-agent-debug
# 	docker run -dit --name arch-agent-debug-c --entrypoint /bin/sh arch-agent-debug -lc "sleep infinity"
# 	docker exec -it arch-agent-debug-c /bin/sh

test-msg-evaluator: build
	clear
	@echo "\nTesting Message Evaluator with Slack event..."
	sam local invoke SlackMessageEvaluator -e events/test-lmbd-msg-evaluator_no_tag.json --env-vars ../env.json --container-host-interface 0.0.0.0 --add-host=host.docker.internal:host-gateway
	sam local invoke SlackMessageEvaluator -e events/test-lmbd-msg-evaluator_tag_history.json --env-vars ../env.json --container-host-interface 0.0.0.0 --add-host=host.docker.internal:host-gateway

check_queue:
	@echo "\nChecking SQS queue for messages..."
	aws sqs receive-message --queue-url $(SQS_QUEUE_URL) --max-number-of-messages 10 --wait-time-seconds 20 | jq

run_local:
	clear
	docker image prune -f
	docker container prune -f
	docker stop $$(docker ps -q) || true
	docker rm $$(docker ps -a -q) || true
	-sudo kill -9 $$(lsof -t -i :3000) 2>/dev/null || true
	@echo "Starting SAM local API server with auto-reload..."
	@echo "Console logs will appear below:"
	@echo "================================"
# 	sam build && sam local start-api --env-vars ../env.json --warm-containers LAZY --host 0.0.0.0 --container-host-interface 0.0.0.0 --add-host=host.docker.internal:host-gateway 2>&1 | tee -a logs/local-api.log
	nodemon --watch ./ --ext js,py,json,yml,yaml --ignore logs/ --exec "sam build && sam local start-api --env-vars ../env.json --warm-containers EAGER --host 0.0.0.0 --container-host-interface 0.0.0.0 --add-host=host.docker.internal:host-gateway 2>&1 | tee -a logs/local-api.log"
# local-api-$(shell date '+%Y-%m-%d-%H-%M-%S').log

#  --debug
install-nodemon:
	@echo "Installing nodemon globally..."
	npm install -g nodemon

ng:
	@echo "Starting ngrok tunnel with response header handling..."
	ngrok http 3000 --request-header-remove challenge --response-header-add "Content-Type: application/json"

validate:
	sam validate --template template.yaml

create-env-example:
	@grep -v '^#' ../.env | grep -v '^$$' | sed 's/=.*/=/' > .env.example

set_credentials:
	@echo "Setting AWS credentials ..."
	aws configure set aws_access_key_id $(AWS_ACCESS_KEY_ID)
	aws configure set aws_secret_access_key $(AWS_SECRET_ACCESS_KEY)
	aws configure set aws_session_token $(AWS_SESSION_TOKEN)
	aws configure set region $(AWS_REGION)
	cat ~/.aws/credentials

c2f:
	mkdir -p diagrams
	@for dir in lmbd*/; do \
		if [ -d "$$dir" ]; then \
			echo "Processing $$dir..."; \
			code2flow ./$$dir/*.py --language=py --no-trimming --exclude-functions="__init__,test_athena_manager,test_s3_manager,test_agent" --output=diagrams/$$(basename $$dir)_code2flow.png; \
		fi; \
	done

export_env:
	@echo "Exporting vars..."
	@set -a; . ./.env; set +a; \
		echo "All variables exported."; \
		bash

list_tools_gh_mcp:
	@echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
	| docker exec -i $(SERVER_CONTAINER) /server/github-mcp-server stdio \
	| jq -r --color-output '.result.tools[] | "\u001b[1;36m\(.name)\u001b[0m\n  \u001b[1;33mdoc:\u001b[0m \(.description // "-")\n  \u001b[1;32margs:\u001b[0m \(.inputSchema)"'
