"""Apply the camera initialization fix after idf.py reconfigure fetched components."""
from pathlib import Path
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--firmware',type=Path,default=Path(__file__).resolve().parents[1]/'firmware')
root=parser.parse_args().firmware.resolve()
path=root/'managed_components/espressif__esp_cam_sensor/src/driver_dvp/esp_cam_ctlr_dvp_cam.c'
source=path.read_text(encoding='utf-8')
marker='/* Dania: an ISR may run immediately when its GPIO handler is installed. */'
queue='''    ctlr->event_queue = xQueueCreate(DVP_CAM_EVENT_QUEUE_SIZE, sizeof(dvp_cam_event_t));
    ESP_GOTO_ON_FALSE(ctlr->event_queue, ESP_ERR_NO_MEM, fail5, TAG, "failed to create event queue");
'''
anchor='    /* Ignore result if this calling fails, maybe users call this in previous step */'
cleanup='fail6:\n    vQueueDelete(ctlr->event_queue);\nfail5:'
free='fail2:\n    heap_caps_free(ctlr->dma_desc);'
early=queue.replace('ESP_ERR_NO_MEM, fail5,','ESP_ERR_NO_MEM, fail2,')
fixed_free='fail2:\n    if (ctlr->event_queue) vQueueDelete(ctlr->event_queue);\n    heap_caps_free(ctlr->dma_desc);'
if marker in source:
    if source.count(marker)!=1 or early not in source or fixed_free not in source:
        raise SystemExit('Incomplete camera patch; inspect the dependency before retrying')
else:
    if any(source.count(expected)!=1 for expected in (queue,anchor,cleanup,free)):
        raise SystemExit('Camera driver no longer matches the pinned patch')
    source=source.replace(queue,'',1).replace(anchor,'    '+marker+'\n'+early+'\n'+anchor,1)
    source=source.replace(cleanup,'fail6:\nfail5:',1).replace(free,fixed_free,1)
    path.write_text(source,encoding='utf-8')
print('Managed camera patch ready')
