from flask import Flask, render_template, request, redirect, url_for, Response, send_from_directory, jsonify, flash
from flask_login import LoginManager, current_user, login_user, login_required
from werkzeug.utils import secure_filename
from werkzeug.urls import url_quote_plus
import os
import time
import logging
import datetime
import random
import threading
import subprocess
from . import config
from . import database
from . import oracle
from . import utils

app = Flask(__name__)


UPLOAD_FOLDER = 'uploads'
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_get"
app.config["SECRET_KEY"] = os.urandom(12)
ALLOWED_EXTENSIONS = set(['mp3', 'png', 'jpg', 'jpeg', 'gif'])

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@login_manager.user_loader
def load_user(id):
	return database.User.from_id(int(id))

started_at_timestamp = time.time()
started_at = datetime.datetime.utcnow()

page_title = "Infinite Glitch - The World's Longest Recorded Pop Song, by Chris Butler."
og_description = """I don't remember if he said it or if I said it or if the caffeine said it but suddenly we're both giggling 'cause the problem with the song isn't that it's too long it's that it's too short."""	
meta_description = """I don't remember if he said it or if I said it or if the caffeine said it but suddenly we're both giggling 'cause the problem with the song isn't that it's too long it's that it's too short."""	

# Import some stuff from the old package
import fore.assetcompiler
from . import database
app.jinja_env.globals["compiled"] = fore.assetcompiler.compiled

def couplet_count(lyrics):
	total = 0
	for count in lyrics:
		total += count.track_lyrics['couplet_count']
	return total

@app.route("/")
def home():
	lyrics = database.get_all_lyrics()
	complete_length = datetime.timedelta(seconds=int(database.get_complete_length()))
	return render_template("index.html",
		open=True, # Can have this check for server load if we ever care
		endpoint="http://localhost:8889/all.mp3", # TODO: Make configurable (or better still, multiplex the port)
		complete_length=complete_length,
		couplet_count=couplet_count(lyrics),
		lyrics=lyrics,
		og_url=config.server_domain,
		og_description=og_description,
		meta_description=meta_description
	)

def _make_route(dir):
	# Use a closure to early-bind the 'dir'
	def non_caching_statics(path):
		response = send_from_directory("../"+dir, path)
		response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
		response.headers['Pragma'] = 'no-cache'
		return response
	app.add_url_rule('/'+dir+'/<path:path>', 'non_caching_'+dir, non_caching_statics)
for _dir in ("audio", "audition_audio", "transition_audio"):
	# audition_audio and transition_audio aren't currently used, but
	# will be part of the admin panel that we haven't yet ported.
	_make_route(_dir)

@app.route("/artwork/<int:id>.jpg")
def track_artwork(id):
	art = database.get_track_artwork(int(id))
	# TODO: If the track hasn't been approved yet, return 404 unless the user is an admin.
	if not art:
		return redirect('../static/img/Default-artwork-200.png')
	return bytes(art)

@app.route("/timing.json")
def timing():
	return jsonify({"time": time.time() * 1000})

@app.route("/credits")
def credits():
	og_description="The world's longest recorded pop song. (Credits)"
	page_title="Credits: Infinite Glitch - the world's longest recorded pop song, by Chris Butler."
	meta_description="The people below are partially responsible for bringing you Infinite Glitch - the world's longest recorded pop song."
	og_url="http://www.infiniteglitch.net/credits"
	return render_template("credits.html",
				og_description=og_description, page_title=page_title,
				meta_description=meta_description,og_url=og_url)

@app.route("/view_artist/<artist>")
def tracks_by_artist(artist):
	# TODO: Clean up the whole sposplit/fposplit stuff, maybe by slash-separating
	artist_for_db = url_artist = artist
	if artist[:8] == 'sposplit':
		artist = artist[9:]
		artist_formatting = artist.split('fposplit',1)
		artist_for_db = ', '.join([part.strip() for part in artist_formatting])
		artist = ' '.join([part.strip() for part in artist_formatting[::-1]])
	tracks_by = database.tracks_by(artist_for_db)
	og_description= artist+" contributions to The world's longest recorded pop song."
	page_title=artist+": Infinite Glitch - the world's longest recorded pop song, by Chris Butler."
	meta_description="Browse the artists who have added to the Infinite Glitch - the world's longest recorded pop song."
	og_url = url_for("tracks_by_artist", artist=url_artist)
	return render_template("view_artist.html", tracks_by=tracks_by, og_description=og_description, 
				page_title=page_title, meta_description=meta_description, og_url=og_url)


@app.route("/choice_chunks")
def choice_chunks():
	og_description= "You can select any individual chunk of The Infinite Glitch to listen to."
	page_title="Browse Artists: Infinite Glitch - the world's longest recorded pop song, by Chris Butler."
	meta_description="You can select any individual chunk of The Infinite Glitch to listen to."
	og_url=config.server_domain+"/choice_chunks"
	letter = request.args.get("letters", "")
	if letter:
		artist_tracks = database.browse_tracks(letter)
		ordered_artists = utils.alphabetize_ignore_the(artist_tracks)
	else:
		ordered_artists = ""
	recent_submitters = database.get_recent_tracks(10)
	ordered_submitters = utils.alphabetize_ignore_the(recent_submitters)
	return render_template("choice_chunks.html", recent_submitters=ordered_submitters, artist_tracks=ordered_artists, letter=letter,
				og_description=og_description, page_title=page_title, meta_description=meta_description, og_url=og_url)

@app.route("/login")
def login_get():
	return render_template("login.html")

@app.route("/login", methods=["POST"])
def login_post():
	user = database.User.from_credentials(request.form["email"], request.form["password"])
	if user: login_user(user)
	return redirect("/")

@app.route("/create_account")
def create_account_get():
	return render_template("create_account.html", page_title="Glitch Account Sign-Up")

@app.route("/create_account", methods=["POST"])
def create_account_post():
	if request.form["password"] != request.form["password2"]:
		return redirect("/create_account")
	info = database.create_user(request.form["username"], request.form["email"], request.form["password"])
	if isinstance(info, str):
		# There's an error.
		return render_template("create_account.html", page_title="Glitch Account Sign-Up", error=info)
	# TODO: Send the email
	# mailer.AlertMessage(admin_message, 'New Account Created')
	confirmation_url = request.base_url + "confirm/%s/%s" % info
	user_message = """Either you or someone else just created an account at InfiniteGlitch.net.

To confirm for %s at %s, please visit %s""" % (request.form["username"], request.form["email"], confirmation_url)
	# mailer.AlertMessage(user_message, 'Infinite Glitch Account', you=submitter_email)
	return render_template("account_confirmation.html")
	
@app.route("/reset_password")
def reset_password_get():
	return render_template("reset_password.html", page_title="Reset Glitch Account Password")

@app.route("/reset_password", methods=["POST"])
def reset_password_post():
	if not request.form["email"]:
		return redirect("/reset_password")
	notice = "Password reset link sent. Please check your email."
	return render_template("account_confirmation.html", notice=notice)

@app.route("/submit")
@login_required
def submit_track_get():
	'''
	The following two forms are for user to submit a track.
	'''
	f = open('fortunes.txt', 'r')
	fortunes = [line for line in f if not line[0] == '%']
	saying = random.choice(fortunes)
	return render_template("submit_track.html", page_title="Infinite Glitch Track Submission Form", witty_saying=saying)

@app.route("/submit", methods=["POST"])
@login_required
def submit_track_post():
	# check if the post request has the file part
	if 'mp3_file' not in request.files:
		flash('No audio file uploaded')
		return redirect(request.url)
	file = request.files["mp3_file"]
	# if user does not select file, browser also
	# submit a empty part without filename
	if not file.filename:
		flash('No audio file uploaded')
		return redirect(request.url)
	if not file.filename.endswith('.mp3') or file.mimetype != "audio/mp3":
		# TODO: Support more files
		# TODO: Test file content, not just extension
		flash('Only .mp3 files currently accepted')
		return redirect(request.url)
	image = None # TODO
	id = database.create_track(file.read(), secure_filename(file.filename), request.form, image, current_user.username)
	# TODO: Send email to admins requesting curation (with the track ID)
	return render_template("confirm_submission.html")
	
@app.route("/recorder")
@login_required
def recorder_get():
	return render_template("recorder.html", page_title="Infinite Glitch Recording Studio")

@app.route("/recorder", methods=["POST"])
@login_required
def recorder_post():
	try:
		print(request.files["data"])
	except:
		print ('not that')
	print(request.files.lists().next())
	# <generator object MultiDict.lists at 0x114c71a98>
	print(request.files.keys())
	# <dict_keyiterator object at 0x114c57278>
	print(request.files.values().next())
	# <generator object MultiDict.values at 0x114c71a98>
	print(current_user if current_user else 'glitch hacker')
	# <flask_login.mixins.AnonymousUserMixin object at 0x111216e80>
	return render_template("recorder.html")
	
@app.route("/oracle", methods=["GET"])
def oracle_get():
	popular_words = oracle.popular_words(90)
	random.shuffle(popular_words)
	question = request.args.get("question")
	print(question)
	if not len(question) == 0:
		question = question
		show_cloud="block"
		answer = oracle.the_oracle_speaks(question)
		if answer.couplet['artist'].name['name_list'][0] == '':
			artist = answer.couplet['artist'].name['name_list'][1]
		else:
			artist = ' name_part_two '.join(answer.couplet['artist'].name['name_list']).strip()
		og_description="Asked the glitch oracle: '"+question+"' and am told '"+answer.couplet['couplet'][0]+answer.couplet['couplet'][1]+"'"
		page_title="The Glitch Oracle - Psychic Answers from the Infinite Glitch"
		meta_description="Asked the glitch oracle: '"+question+"' and am told '"+answer.couplet['couplet'][0]+answer.couplet['couplet'][1]+"'"
		og_url="http://www.infiniteglitch.net/share_oracle/"+url_quote_plus(question)+"/"+url_quote_plus(answer.couplet['couplet'][0])+"/"+url_quote_plus(answer.couplet['couplet'][1])+"/"+url_quote_plus(artist)
		print(answer.couplet['artist'].name['display_name'])
		print(answer.couplet['couplet'][0])
		print(question)
		print(111111111)
		redirect(og_url)
	else:
		question, answer = ("","")
		show_cloud="none"
		page_title="Ask The Glitch Oracle"
		og_description="Ask The Glitch Oracle"
		meta_description="Ask The Glitch Oracle"
		og_url="http://www.infiniteglitch.net/oracle"
	return render_template("oracle.html", page_title="Glitch Oracle", question=question, 
							answer=answer, popular_words=popular_words[:90],
							show_cloud=show_cloud, og_description=og_description, 
							meta_description=meta_description, og_url=og_url, url_quote_plus=url_quote_plus)


def run(port=config.http_port, disable_logins=False):
	if not os.path.isdir("glitch/static/assets"):
		os.mkdir("glitch/static/assets")
	if disable_logins:
		app.config['LOGIN_DISABLED'] = True
	app.run(host="0.0.0.0", port=port)

if __name__ == '__main__':
	run()
