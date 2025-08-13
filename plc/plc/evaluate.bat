@echo off
echo error_rate(%%), without plc, g711 plc > g711plc.csv
..\Debug\plc 0
python cal_pesq.py reference.wav log\0_without_plc.wav log\0_with_plc_g711.wav >> g711plc.csv
..\Debug\plc 10
python cal_pesq.py reference.wav log\10_without_plc.wav log\10_with_plc_g711.wav --error_rate 10>> g711plc.csv
..\Debug\plc 20
python cal_pesq.py reference.wav log\20_without_plc.wav log\20_with_plc_g711.wav --error_rate 20>> g711plc.csv
..\Debug\plc 30
python cal_pesq.py reference.wav log\30_without_plc.wav log\30_with_plc_g711.wav --error_rate 30>> g711plc.csv
..\Debug\plc 40
python cal_pesq.py reference.wav log\40_without_plc.wav log\40_with_plc_g711.wav --error_rate 40>> g711plc.csv
..\Debug\plc 50
python cal_pesq.py reference.wav log\50_without_plc.wav log\50_with_plc_g711.wav --error_rate 50>> g711plc.csv
..\Debug\plc 60
python cal_pesq.py reference.wav log\60_without_plc.wav log\60_with_plc_g711.wav --error_rate 60>> g711plc.csv
..\Debug\plc 70
python cal_pesq.py reference.wav log\70_without_plc.wav log\70_with_plc_g711.wav --error_rate 70>> g711plc.csv
..\Debug\plc 80
python cal_pesq.py reference.wav log\80_without_plc.wav log\80_with_plc_g711.wav --error_rate 80>> g711plc.csv
..\Debug\plc 90
python cal_pesq.py reference.wav log\90_without_plc.wav log\90_with_plc_g711.wav --error_rate 90>> g711plc.csv
echo on