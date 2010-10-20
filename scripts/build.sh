# This script should be called from within Hudson

cd $WORKSPACE
VENV=$WORKSPACE/venv

echo "Starting build on executor $EXECUTOR_NUMBER..." `date`

if [ -z $1 ]; then
    echo "Warning: You should provide a unique name for this job to prevent database collisions."
    echo "Usage: ./build.sh <name>"
    echo "Continuing, but don't say you weren't warned."
fi

echo "Setup..." `date`

# Make sure there are no old pyc files around.
find . -name '*.pyc' | xargs rm

if [ ! -d "$VENV/bin" ]; then
    echo "No virtualenv found.  Making one..."
    virtualenv $VENV
fi

source $VENV/bin/activate

# TODO: temporary until we make a compiled.txt
pip install -q mysql-python
#pip install -q -r requirements/compiled.txt

pushd vendor && git pull && git submodule update --init && popd

# Create paths we want for addons
if [ ! -d "/tmp/flightdeck" ]; then
    mkdir /tmp/flightdeck
fi

cat > settings_local.py <<SETTINGS
from settings import *
ROOT_URLCONF = 'workspace.urls'
DATABASES['default']['NAME'] = 'builder_pamo'
DATABASES['default']['TEST_NAME'] = 'test_builder_pamo'
DATABASES['default']['TEST_CHARSET'] = 'utf8'
DATABASES['default']['TEST_COLLATION'] = 'utf8_general_ci'
CACHE_BACKEND = 'dummy://'

UPLOAD_DIR = '/tmp/flightdeck'

SETTINGS

echo "Starting tests..." `date`
export FORCE_DB='yes sir'

# with-coverage excludes sphinx so it doesn't conflict with real builds.
if [[ $2 = 'with-coverage' ]]; then
    coverage run manage.py test --noinput --logging-clear-handlers --with-xunit -a'!sphinx'
    coverage xml $(find apps lib -name '*.py')
else
    python manage.py test --noinput --logging-clear-handlers --with-xunit
fi

echo 'voila!'
