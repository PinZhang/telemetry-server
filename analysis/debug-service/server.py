#!/usr/bin/env python

from argparse import ArgumentParser
from flask import Flask, render_template, request, redirect, url_for
from flask.ext.login import LoginManager, login_required, current_user
from flask.ext.browserid import BrowserID
from user import User, AnonymousUser
from boto.ec2 import connect_to_region as ec2_connect
from boto.ses import connect_to_region as ses_connect
from boto.s3 import connect_to_region as s3_connect
from urlparse import urljoin
from uuid import uuid4

# Create flask app
app = Flask(__name__)
app.config.from_object('config')

# Connect to AWS
ec2 = ec2_connect(app.config['AWS_REGION'])
ses = ses_connect('us-east-1') # only supported region!
s3  = s3_connect(app.config['AWS_REGION'])
bucket = s3.get_bucket(app.config['TEMPORARY_BUCKET'], validate = False)

# Create login manager
login_manager = LoginManager()
login_manager.anonymous_user = AnonymousUser

# Initialize browser id login
browser_id = BrowserID()

def abs_url_for(rule, **options):
    return urljoin(request.url_root, url_for(rule, **options))

@browser_id.user_loader
def get_user(response):
    """Create User from BrowserID response"""
    if response['status'] == 'okay':
        return User(response['email'])
    return User(None)

@login_manager.user_loader
def load_user(email):
    """Create user from already authenticated email"""
    return User(email)

@login_manager.unauthorized_handler
def unauthorized():
    return render_template('index.html')

# Routes
@app.route('/', methods=["GET"])
def index():
    return render_template('index.html')

@app.route("/schedule", methods=["GET"])
@login_required
def schedule_job():
    # Check that the user logged in is also authorized to do this
    if not current_user.is_authorized():
        return login_manager.unauthorized()
    return render_template('schedule.html')

def get_required_int(request, field, label, min_value=0, max_value=100):
    value = request.form[field]
    if value is None or value.strip() == '':
        raise ValueError(label + " is required")
    else:
        try:
            value = int(value)
            if value < min_value or value > max_value:
                raise ValueError("{0} should be between {1} and {2}".format(label, min_value, max_value))
        except ValueError:
            raise ValueError("{0} should be an int between {1} and {2}".format(label, min_value, max_value))
    return value

def hour_to_time(hour):
    return "{0}:00 UTC".format(hour)

def display_dow(dow):
    if dow is None:
        return ''

    dayname = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][dow]
    return " every {0}".format(dayname)

def display_dom(dom):
    if dom is None:
        return ''
    nth = "{0}th".format(dom)
    if dom % 10 == 1:
        nth = "{0}st".format(dom)
    elif dom % 10 == 2:
        nth = "{0}nd".format(dom)
    elif dom % 10 == 3:
        nth = "{0}rd".format(dom)
    return " on the {0} day of each month".format(nth)

@app.route("/schedule/new", methods=["POST"])
@login_required
def create_scheduled_job():
    # Check that the user logged in is also authorized to do this
    if not current_user.is_authorized():
        return login_manager.unauthorized()

    errors = {}
    for f in ['job-name', 'commandline', 'output-dir',
              'schedule-frequency', 'schedule-time-of-day', 'timeout']:
        val = request.form[f]
        if val is None or val.strip() == '':
            errors[f] = "This field is required"

    time_of_day = -1
    try:
        time_of_day = get_required_int(request, 'schedule-time-of-day',
                "Time of Day", max_value=23)
    except ValueError, e:
        errors['schedule-time-of-day'] = e.message

    frequency = request.form['schedule-frequency'].strip()
    # m h  dom mon dow   command
    cron_bits = [0, time_of_day]
    day_of_week = None
    day_of_month = None
    if frequency == 'weekly':
        # day of week is required
        try:
            day_of_week = get_required_int(request, 'schedule-day-of-week',
                    "Day of Week", max_value=6)
            cron_bits.extend(['*', '*', day_of_week])
        except ValueError, e:
            errors['schedule-day-of-week'] = e.message
    elif frequency == 'monthly':
        # day of month is required
        try:
            day_of_month = get_required_int(request, 'schedule-day-of-month',
                    "Day of Month", max_value=31)
            cron_bits.extend([day_of_month, '*', '*'])
        except ValueError, e:
            errors['schedule-day-of-month'] = e.message
    elif frequency != 'daily':
        # incoming value is bogus.
        errors['schedule-frequency'] = "Pick one of the values in the list"
    else:
        cron_bits.extend(['*', '*', '*'])

    try:
        timeout = get_required_int(request, 'timeout',
                "Job Timeout", max_value=24*60)
    except ValueError, e:
        errors['timeout'] = e.message

    # Check for code-tarball
    if request.files['code-tarball']:
        filename = request.files['code-tarball'].filename
        if not (filename.endswith(".tar.gz") or filename.endswith(".tgz")):
            errors['code-tarball'] = "Code file must be in .tar.gz or .tgz format"
    else:
        errors['code-tarball'] = "File is required (.tar.gz or .tgz)"

    # TODO: Check if job_name is already in use

    # If there were any errors, stop and re-display the form.
    # TODO: It would be polite to render the form with the previously-supplied
    #       values filled in.
    if errors:
        return render_template('schedule.html', errors=errors)

    # Now do it!
    # What do we need to know about a job?
    # job_owner
    # job_id
    # job_schedule
    # job_name
    # job_timeout_minutes
    # job_code_uri
    # job_commandline
    # job_data_bucket (telemetry-public-analysis)
    # job_output_dir

    cron_bits.append("/path/to/run/script.sh")

    code_s3path = "s3://telemetry-analysis-code/{0}/{1}".format(request.form["job-name"], request.files["code-tarball"].filename)
    data_s3path = "s3://telemetry-public-analysis/{0}/data/".format(request.form["job-name"])
    return render_template('schedule_create.html',
        code_s3path = code_s3path,
        data_s3path = data_s3path,
        commandline = request.form['commandline'],
        output_dir = request.form['output-dir'],
        job_frequency = frequency,
        job_time = hour_to_time(time_of_day),
        job_dow = display_dow(day_of_week),
        job_dom = display_dom(day_of_month),
        job_timeout = timeout,
        cron_spec = " ".join([str(c) for c in cron_bits])
        )

@app.route("/worker", methods=["GET"])
@login_required
def get_worker_params():
    # Check that the user logged in is also authorized to do this
    if not current_user.is_authorized():
        return login_manager.unauthorized()
    return render_template('worker.html', token = str(uuid4()))

@app.route("/worker/new", methods=["POST"])
@login_required
def spawn_worker_instance():
    # Check that the user logged in is also authorized to do this
    if not current_user.is_authorized():
        return login_manager.unauthorized()

    errors = {}

    # Check required fields
    for f in ['name', 'token']:
        val = request.form[f]
        if val is None or val.strip() == '':
            errors[f] = "This field is required"

    # Check required file
    if not request.files['public-ssh-key']:
        errors['code-tarball'] = "Public key file is required"

    # TODO: Bug 961200: Check that a proper OpenSSH public key was uploaded.
    # It should start with "ssh-rsa AAAAB3"
    pubkey = request.files['public-ssh-key'].read()
    if not pubkey.startswith("ssh-rsa AAAAB3"):
        print "Found a pubkey of:", pubkey
        errors['public-ssh-key'] = "Supplied file does not appear to be a valid OpenSSH public key."

    if errors:
        return render_template('worker.html', errors=errors, token=str(uuid4()))

    # Upload s3 key to bucket
    sshkey = bucket.new_key("keys/%s.pub" % request.form['token'])
    sshkey.set_contents_from_string(pubkey)

    # Create
    boot_script = render_template('boot-script.sh',
        aws_region          = app.config['AWS_REGION'],
        temporary_bucket    = app.config['TEMPORARY_BUCKET'],
        ssh_key             = sshkey.key
    )

    # Create EC2 instance
    reservation = ec2.run_instances(
        image_id                                = 'ami-ace67f9c',
        security_groups                         = app.config['SECURITY_GROUPS'],
        user_data                               = boot_script,
        instance_type                           = app.config['INSTANCE_TYPE'],
        instance_initiated_shutdown_behavior    = 'terminate',
        client_token                            = request.form['token'],
        instance_profile_name                   = app.config['INSTANCE_PROFILE']
    )
    instance = reservation.instances[0]

    # Associate a few tags
    ec2.create_tags([instance.id], {
        "Owner":            current_user.email,
        "Name":             request.form['name'],
        "Application":      app.config['INSTANCE_APP_TAG']
    })

    # Send an email to the user who launched it
    params = {
        'monitoring_url':   abs_url_for('monitor', instance_id = instance.id)
    }
    ses.send_email(
        source          = app.config['EMAIL_SOURCE'],
        subject         = ("telemetry-analysis worker instance: %s (%s) launched"
                           % (request.form['name'], instance.id)),
        format          = 'html',
        body            = render_template('instance-launched-email.html', **params),
        to_addresses    = [current_user.email]
    )
    return redirect(url_for('monitor', instance_id = instance.id))

@app.route("/worker/monitor/<instance_id>", methods=["GET"])
@login_required
def monitor(instance_id):
    # Check that the user logged in is also authorized to do this
    if not current_user.is_authorized():
        return  login_manager.unauthorized()

    try:
        # Fetch the actual instance
        reservations = ec2.get_all_reservations(instance_ids = [instance_id])
        instance = reservations[0].instances[0]
    except IndexError:
        return "No such instance"

    # Check that it is the owner who is logged in
    if instance.tags['Owner'] != current_user.email:
        return  login_manager.unauthorized()

    # Alright then, let's report status
    return render_template(
        'monitor.html',
        instance_state  = instance.state,
        public_dns      = instance.public_dns_name,
        terminate_url   = abs_url_for('kill', instance_id = instance.id)
    )

@app.route("/worker/kill/<instance_id>", methods=["GET"])
@login_required
def kill(instance_id):
    # Check that the user logged in is also authorized to do this
    if not current_user.is_authorized():
        return  login_manager.unauthorized()

    try:
        # Fetch the actual instance
        reservations = ec2.get_all_reservations(instance_ids = [instance_id])
        instance = reservations[0].instances[0]
    except IndexError:
        return "No such instance"

    # Check that it is the owner who is logged in
    if instance.tags['Owner'] != current_user.email:
        return login_manager.unauthorized()

    # Terminate and update instance
    instance.terminate()
    instance.update()

    # Alright then, let's report status
    return render_template(
        'kill.html',
        instance_state  = instance.state,
        public_dns      = instance.public_dns_name,
        monitoring_url  = abs_url_for('monitor', instance_id = instance.id)
    )

@app.route("/status", methods=["GET"])
def status():
    return "OK"

login_manager.init_app(app)
browser_id.init_app(app)

if __name__ == '__main__':
    parser = ArgumentParser(description='Launch Telemetry Analysis Service')
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=80, type=int)
    args = parser.parse_args()

    app.run(host = args.host, port = args.port, debug=True)
