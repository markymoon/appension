# Import smtplib for the actual sending function
import smtplib
from email.mime.text import MIMEText
from . import apikeys

def AlertMessage(message, subject='Glitch System Message', me=apikeys.system_email, you=apikeys.admin_email):
	msg = MIMEText(message)

	msg['Subject'] = subject
	msg['From'] = me
	msg['To'] = you

	# Send the message via our own SMTP server, but don't include the
	# envelope header.
	s = smtplib.SMTP(apikeys.SMTP_SERVER_PORT)
	if not apikeys.SMTP_SERVER_PORT == 'localhost':
		s.ehlo()
		s.starttls()
		s.login(apikeys.SMTP_USERNAME, apikeys.SMTP_PASSWORD)
	s.sendmail(me, [you], msg.as_string())
	s.quit()
	
def SubmitterMessage(message, subject='Major Glitch going Infinite!', me=apikeys.system_email, you=apikeys.admin_email, test=1):
	msg = MIMEText(message)

	msg['Subject'] = subject
	msg['From'] = me
	if not test:
		msg['To'] = you
	else:
		msg['To'] = me

	# Send the message via our own SMTP server, but don't include the
	# envelope header.
	s = smtplib.SMTP(apikeys.SMTP_SERVER_PORT)
	if not apikeys.SMTP_SERVER_PORT == 'localhost':
		s.ehlo()
		s.starttls()
		s.login(apikeys.SMTP_USERNAME, apikeys.SMTP_PASSWORD)
	s.sendmail(me, [you], msg.as_string())
	s.quit()
	
def test():
	a_message = 'There is someting I need to tell you.'
	AlertMessage(a_message)

if not hasattr(apikeys, "SMTP_SERVER_PORT"):
	# If we don't have a server configured, neuter the mailing functions.
	# TODO: Should this print a warning on startup?
	def AlertMessage(*a, **kw): pass
	def SubmitterMessage(*a, **kw): pass
