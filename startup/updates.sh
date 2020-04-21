#! /bin/bash
sudo apt upgrade --assume-yes
sudo apt autoremove --assume-yes
cd '/home/conradi_matic/Alpha/'
git pull origin master
source ./env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -U -r requirements.txt
