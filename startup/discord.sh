#! /bin/bash
cd '/home/conradi_matic/Alpha/'
source ./env/bin/activate
while true; do
		git pull origin master
		python ./discord_alpha.py
		sleep 5
done
