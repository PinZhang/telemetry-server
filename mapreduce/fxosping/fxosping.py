import json

def map(key, dims, value, context):
    # get info object
    data = json.loads(value)
    if data.has_key('info'):
        data = data.get('info')

    reason, appName, appUpdateChannel, appVersion, appBuildID, submission_date = dims

    def dataval(key):
        return data.get(key, 'unknown')

    def strval(d, key):
        if not d:
            return 'unknown'

        return d.get(key, 'unknown') or 'unknown'

    hours = -1
    time_to_ping = 'unknown'
    if 'pingTime' in data and 'activationTime' in data:
        # Time to ping in hours
        hours = float(int(data['pingTime']) - int(data['activationTime'])) / (60 * 60 * 1000)
        time_to_ping = '%d' % round(hours)

    result = []
    result.append(submission_date)
    result.append(strval(data, 'deviceinfo.os'))
    result.append(strval(data, 'deviceinfo.software'))
    result.append(time_to_ping)
    result.append(dataval('screenWidth'))
    result.append(dataval('screenHeight'))
    result.append(dataval('devicePixelRatio'))
    result.append(strval(data, 'locale'))
    result.append(strval(data, 'deviceinfo.hardware'))
    result.append(strval(data, 'deviceinfo.product_model'))
    result.append(strval(data, 'deviceinfo.firmware_revision'))
    result.append(appUpdateChannel)

    icc = data.get('icc')
    result.append(strval(icc, 'mnc'))
    result.append(strval(icc, 'mcc'))
    result.append(strval(icc, 'spn'))

    network = data.get('network')
    result.append(strval(network, 'mnc'))
    result.append(strval(network, 'mcc'))
    result.append(strval(network, 'operator'))

    result.append(strval(data, 'geoCountry'))
    context.write(key, result)

def setup_reduce(context):
    context.field_separator = ','

def reduce(key, values, context):
    for v in values:
        context.writecsv(v)
