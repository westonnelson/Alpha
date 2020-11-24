docker image prune -f
COMPOSE_DOCKER_CLI_BUILD=1 docker-compose build
docker tag alphabotsystem/alpha-data-server gcr.io/nlc-bot-36685/alpha-data-server
docker tag alphabotsystem/alpha-discord-bot gcr.io/nlc-bot-36685/alpha-discord-bot
docker tag alphabotsystem/alpha-discord-manager gcr.io/nlc-bot-36685/alpha-discord-manager
docker tag alphabotsystem/alpha-satellites gcr.io/nlc-bot-36685/alpha-satellites