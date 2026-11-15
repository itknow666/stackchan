"""Provision the local MQTT integration through Home Assistant's config flow."""
import json
import logging
from pathlib import Path
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.helpers import config_validation as cv

DOMAIN='dania_setup'
CONFIG_SCHEMA=cv.empty_config_schema(DOMAIN)
_LOGGER=logging.getLogger(__name__)

async def async_setup(hass, config):
    async def setup_mqtt(event):
        try:
            if not hass.config_entries.async_entries('mqtt'):
                raw=await hass.async_add_executor_job(Path(hass.config.path('dania-local.json')).read_text)
                cfg=json.loads(raw)
                flow=await hass.config_entries.flow.async_init('mqtt',context={'source':'user'})
                if flow.get('type')!='form' or flow.get('step_id')!='broker':
                    raise RuntimeError('Unexpected MQTT configuration step')
                data=flow['data_schema']({'broker':'127.0.0.1','port':1883,'protocol':'5',
                    'username':cfg['mqtt_user'],'password':cfg['mqtt_password'],
                    'other_settings':{'set_client_cert':False,'set_ca_cert':'off','transport':'tcp'}})
                result=await hass.config_entries.flow.async_configure(flow['flow_id'],data)
                if result.get('type')!='create_entry':
                    raise RuntimeError('MQTT setup failed: '+str(result.get('errors',{})))
            _LOGGER.info('Dania local MQTT configured')
        except Exception as exc:
            _LOGGER.error('Dania setup failed: %s',type(exc).__name__)

    async def report(event):
        states=[{'entity_id':s.entity_id,'state':s.state} for s in hass.states.async_all() if '.dania_' in s.entity_id]
        path=Path(hass.config.path('dania-status.json'))
        await hass.async_add_executor_job(path.write_text,json.dumps({'entities':states},ensure_ascii=False))

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED,setup_mqtt)
    from homeassistant.helpers.event import async_track_time_interval
    from datetime import timedelta
    async_track_time_interval(hass,report,timedelta(seconds=20))
    return True
