docker image prune -f
COMPOSE_DOCKER_CLI_BUILD=1 docker-compose build
docker tag alphabotsystem/alpha-parser gcr.io/nlc-bot-36685/alpha-parser
docker tag alphabotsystem/alpha-candle-server gcr.io/nlc-bot-36685/alpha-candle-server
docker tag alphabotsystem/alpha-image-server gcr.io/nlc-bot-36685/alpha-image-server
docker tag alphabotsystem/alpha-quote-server gcr.io/nlc-bot-36685/alpha-quote-server
docker tag alphabotsystem/alpha-detail-server gcr.io/nlc-bot-36685/alpha-detail-server
docker tag alphabotsystem/alpha-trade-server gcr.io/nlc-bot-36685/alpha-trade-server
docker tag alphabotsystem/alpha-cron-jobs gcr.io/nlc-bot-36685/alpha-cron-jobs
docker tag alphabotsystem/alpha-discord-bot gcr.io/nlc-bot-36685/alpha-discord-bot
docker tag alphabotsystem/alpha-discord-manager gcr.io/nlc-bot-36685/alpha-discord-manager
docker tag alphabotsystem/alpha-satellites gcr.io/nlc-bot-36685/alpha-satellites