source /run/secrets/alpha-service/key
if [[ $PRODUCTION_MODE == "1" ]]
then
	python -u app/cron_jobs.py
else
	python -u app/cron_jobs.py
fi