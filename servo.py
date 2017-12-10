#!/usr/bin/python
import time
import RPi.GPIO as GPIO
swlist[16,18,22,24,32,36,38,40]
sw1=16 
sw2=18 
sw3=22 
sw4=24 
sw5=32 
sw6=36 
sw7=38 
sw8=40 
GPIO.setmode(GPIO.BOARD)
MotorPin1=11
MotorPin2=12
MotorPin3=13
MotorPin4=15
val1=7.5
val2=7.5
val3=7.5
val4=7.5
GPIO.setup(swlist,GPIO.IN,pull_up_down=GPIO.PUD_UP) 
GPIO.setup(MotorPin1,GPIO.OUT)
GPIO.setup(MotorPin2,GPIO.OUT)
GPIO.setup(MotorPin3,GPIO.OUT)
GPIO.setup(MotorPin4,GPIO.OUT)
pwm_motor1=GPIO.PWM(MotorPin1, 50)
pwm_motor2=GPIO.PWM(MotorPin2, 50)
pwm_motor3=GPIO.PWM(MotorPin3, 50)
pwm_motor4=GPIO.PWM(MotorPin4, 50)
pwm_motor1.start(7.5)
pwm_motor1.start(7.5)
pwm_motor1.start(7.5)
pwm_motor1.start(7.5)

while 1:
    if GPIO.input(sw1) == GPIO.LOW:
        val1 + 0.1
		if val1 > 11 :
			val1=11
            pwm_motor1=GPIO.PWM(MotorPin1, val1)
            time.sleep(0.05)
	if GPIO.input(sw2) == GPIO.LOW:
        val1 - 0.1
		if val1 < 4 :
			val1=4
            pwm_motor1=GPIO.PWM(MotorPin1, val1)
            time.sleep(0.05)
	if GPIO.input(sw3) == GPIO.LOW:
        val2 + 0.1
		if val2 > 11 :
			val2=11
            pwm_motor2=GPIO.PWM(MotorPin2, val2)
            time.sleep(0.05)
	if GPIO.input(sw4) == GPIO.LOW:
        val2 - 0.1
		if val2 < 4 :
			val1=4
            pwm_motor2=GPIO.PWM(MotorPin2, val2)
            time.sleep(0.05)
GPIO.cleanup()
