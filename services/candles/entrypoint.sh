source /run/secrets/alpha-service/key
if [[ $PRODUCTION_MODE == "1" ]]
then
	python -u app/candle_server.py
else
	python -u app/candle_server.py
fi