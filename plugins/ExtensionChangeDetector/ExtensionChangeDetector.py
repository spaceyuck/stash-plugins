from collections import defaultdict
import json
from typing import Callable
import sys

import stashapi.log as log
from stashapi.stashapp import StashInterface

FOLDER_ITEM_PAGE_SIZE=1000

ROOT_FOLDER_QUERY = """
query FindRootFoldersForSelect($zip_file_filter: MultiCriterionInput) {
  findFolders(
    filter: { per_page: -1, sort: "path", direction: ASC }
    folder_filter: { parent_folder: { modifier: IS_NULL }, zip_file: $zip_file_filter }
  ) {
    count
    folders {
      ...SelectFolderData
      __typename
    }
    __typename
  }
}

fragment SelectFolderData on Folder {
  id
  path
  basename
  __typename
}
"""

SUB_FOLDER_QUERY = """
query FindFoldersForQuery($filter: FindFilterType, $folder_filter: FolderFilterType, $ids: [ID!]) {
  findFolders(filter: $filter, folder_filter: $folder_filter, ids: $ids) {
    count
    folders {
      ...RecursiveFolderData
      __typename
    }
    __typename
  }
}

fragment RecursiveFolderData on Folder {
  ...SelectFolderData
  parent_folders {
    ...SelectFolderData
    __typename
  }
  __typename
}

fragment SelectFolderData on Folder {
  id
  path
  basename
  __typename
}
"""



#
# image methods
#

def processAllImages():
    _process_all(
        get_total_count=_get_image_total_count, get_subtree_item_count=_get_image_subfolder_count, get_folder_items=_get_image_folder_items, get_item=_get_image, update_item=_update_image,
        setting_path_prefix=settings["imagesPathPrefix"], setting_use_phash=settings["imagesUsePhash"]
    )

def _get_image_total_count() -> int:
    return stash.find_images(f={}, filter={"page": 0, "per_page": 0}, get_count=True)[0]

def _get_image_subfolder_count(folder_id) -> int:
    return stash.find_images(
        f={
            "files_filter": {
                "parent_folder": {
                    "depth": -1,
                    "excludes": [],
                    "modifier": "INCLUDES",
                    "value": [
                        folder_id
                    ]
                }
            }
        }, 
        filter={"page": 0, "per_page": 0}, 
        get_count=True
    )[0]

def _get_image_folder_items(folder_id, page) -> list[dict]:
    return stash.find_images(
        f={
            "files_filter": {
                "parent_folder": {
                    "depth": 0,
                    "excludes": [],
                    "modifier": "INCLUDES",
                    "value": [
                        folder_id
                    ]
                }
            }
        }, 
        filter={"page": page, "per_page": FOLDER_ITEM_PAGE_SIZE, "sort": "path"}, 
        fragment='id files:visual_files { ... on BaseFile { path fingerprints { type value } } }'
    )

def _get_image(id: str) -> dict:
    return stash.find_image(id)

def _update_image(changes):
    stash.update_images(changes)

#
# scene methods
#

def processAllScenes():
    _process_all(
        get_total_count=_get_scene_total_count, get_subtree_item_count=_get_scene_subfolder_count, get_folder_items=_get_scene_folder_items, get_item=_get_scene, update_item=_update_scene,
        merge_items=_merge_scenes,
        setting_path_prefix=settings["scenesPathPrefix"], setting_use_phash=settings["scenesUsePhash"], setting_use_merge=True
    )

def _get_scene_total_count() -> int:
    return stash.find_scenes(f={}, filter={"page": 0, "per_page": 0}, get_count=True)[0]

def _get_scene_subfolder_count(folder_id) -> int:
    return stash.find_scenes(
        f={
            "files_filter": {
                "parent_folder": {
                    "depth": -1,
                    "excludes": [],
                    "modifier": "INCLUDES",
                    "value": [
                        folder_id
                    ]
                }
            }
        }, 
        filter={"page": 0, "per_page": 0}, 
        get_count=True
    )[0]

def _get_scene_folder_items(folder_id, page) -> list[dict]:
    return stash.find_scenes(
        f={
            "files_filter": {
                "parent_folder": {
                    "depth": 0,
                    "excludes": [],
                    "modifier": "INCLUDES",
                    "value": [
                        folder_id
                    ]
                }
            }
        }, 
        filter={"page": page, "per_page": FOLDER_ITEM_PAGE_SIZE, "sort": "path"}, 
        fragment='id files { path fingerprints { type value } }'
    )

def _get_scene(id: str) -> dict:
    return stash.find_scene(id)

def _merge_scenes(source_ids, target_id):
    stash.merge_scenes(source_ids, target_id)

def _update_scene(changes):
    stash.update_scene(changes)

#
# generic tree waling and processing
#

def _process_all(
        get_total_count: Callable[[], int], get_subtree_item_count: Callable[[str], int], get_folder_items: Callable[[str], list[dict]], get_item : Callable[[str], dict], update_item : Callable[[dict], None], merge_items : Callable[[list[int], int], dict] = None,
        setting_path_prefix: str = '', setting_use_phash: bool = False, setting_use_merge: bool = False
):
    count_total = get_total_count()
    count_processed = 0

    root_folders = stash.call_GQL(query=ROOT_FOLDER_QUERY)
    for root_folder in root_folders["findFolders"]["folders"]:
        for progress_increment in _process_folder(root_folder,
                get_subtree_item_count=get_subtree_item_count, get_folder_items=get_folder_items, get_item=get_item, update_item=update_item, merge_items=merge_items,
                setting_path_prefix=setting_path_prefix, setting_use_phash=setting_use_phash, setting_use_merge=setting_use_merge
        ):
            count_processed += progress_increment
            log.progress((count_processed / count_total))


def _process_folder(folder: dict[str, str], 
        get_subtree_item_count: Callable[[str], int], get_folder_items: Callable[[str], list[dict]], get_item : Callable[[str], dict], update_item : Callable[[dict], None], merge_items : Callable[[list[int], int], dict] = None,
        setting_path_prefix: str = '', setting_use_phash: bool = False, setting_use_merge: bool = False
):
    # count items in subtree
    item_subtree_count = get_subtree_item_count(folder['id'])

    # skip subtree if no items
    if item_subtree_count == 0:
        return
    
    if setting_path_prefix:
        # current path is within configured prefix
        if folder['path'] == setting_path_prefix or folder['path'].startswith(setting_path_prefix):
            log.debug(f"path prefix \"{setting_path_prefix}\" allows processing \"{folder['path']}\"")
            scan_files=True
        # current path is parent of configured prefix
        elif setting_path_prefix.startswith(folder['path']):
            log.debug(f"path prefix \"{setting_path_prefix}\" subtree-only processing \"{folder['path']}\"")
            scan_files=False
        # current path is unrelated to configured prefix -> skip
        else:
            log.debug(f"path prefix \"{setting_path_prefix}\" excludes processing \"{folder['path']}\"")
            yield item_subtree_count
            return
    else:
        scan_files=True

    if scan_files:
        processed_files = 0
        for progress_increment in _process_folder_files(folder, 
                get_folder_items=get_folder_items, get_item=get_item, update_item=update_item, merge_items=merge_items,
                setting_use_phash=setting_use_phash, setting_use_merge=setting_use_merge
        ):
            processed_files += progress_increment
        yield processed_files

    sub_folders = stash.call_GQL(query=SUB_FOLDER_QUERY, variables={
        "folder_filter": {
            "parent_folder": {
            "value": folder['id'],
            "modifier": "EQUALS"
            }
        },
        "filter": {
            "per_page": -1,
            "sort": "basename",
            "direction": "ASC"
        }
    })

    for sub_folder in sub_folders["findFolders"]["folders"]:
        for progress_increment in _process_folder(sub_folder,
                get_subtree_item_count=get_subtree_item_count, get_folder_items=get_folder_items, get_item=get_item, update_item=update_item, merge_items=merge_items,
                setting_path_prefix=setting_path_prefix, setting_use_phash=setting_use_phash, setting_use_merge=setting_use_merge
        ):
            yield progress_increment


def _process_folder_files(folder: dict[str, str], 
        get_folder_items: Callable[[str], list[dict]], get_item : Callable[[str], dict], update_item : Callable[[dict], None], merge_items : Callable[[list[int], int], dict] = None,
        setting_use_phash: bool = False, setting_use_merge: bool = False
):
    log.info(f"scanning \"{folder['path']}\" for items with changed extensions...")

    item_groups_by_path : dict[str, list] = defaultdict(list)
    processed_ids = set()

    item_count = 0
    item_page = 0
    last_path_without_extension = None
    while item_page < 999999:
        items = get_folder_items(folder['id'], item_page)
        
        if len(items) == 0:
            break

        for item in items:
            item_count += 1

            for item_file in item['files']:
                # skip if file from other path (same file in multiple folders / under multiple names)
                if not item_file['path'].startswith(folder['path']):
                    continue
                
                path_last_dot = item_file['path'].rfind('.')
                # no extension -> skip
                if path_last_dot < 0:
                    continue
                
                # avoid processing an item more than once per folder (for example if scene already has merged duplicates)
                if item['id'] in processed_ids:
                    continue

                path_without_extension = item_file['path'][:path_last_dot]
                item_groups_by_path[path_without_extension].append(item)

                #log.debug(f"processing file \"{item_file['path']}\" in folder \"{folder['path']}\": path \"{path_without_extension}\" has {len(item_groups_by_path[path_without_extension])} group items")

                # traversal is path order, so once the path without extension changes, all variants under that path should have been seen
                # -> process if more than 1 file, then forget
                if last_path_without_extension is not None and path_without_extension != last_path_without_extension:
                    if len(item_groups_by_path[last_path_without_extension]) > 1:
                        _process_item_group(item_groups_by_path[last_path_without_extension], 
                            get_item=get_item, update_item=update_item, merge_items=merge_items,
                            setting_use_phash=setting_use_phash, setting_use_merge=setting_use_merge
                        )
                    yield len(item_groups_by_path[last_path_without_extension])
                    del item_groups_by_path[last_path_without_extension]

                processed_ids.add(item['id'])

                last_path_without_extension = path_without_extension

        item_page += 1

    # process last entry
    if len(item_groups_by_path[last_path_without_extension]) > 1:
        _process_item_group(item_groups_by_path[last_path_without_extension], 
            get_item=get_item, update_item=update_item, merge_items=merge_items,
            setting_use_phash=setting_use_phash, setting_use_merge=setting_use_merge
        )
    yield len(item_groups_by_path[last_path_without_extension])
    del item_groups_by_path[last_path_without_extension]


def _process_item_group(items: list, 
        get_item : Callable[[str], dict], update_item : Callable[[dict], None], merge_items : Callable[[list[int], int], dict] = None,
        setting_use_phash: bool = False, setting_use_merge: bool = False
):
    if setting_use_phash:
        items_by_phash = defaultdict(list)
        for item in items:
            for item_file in item['files']:
                for fingerprint in item_file['fingerprints']:
                    if fingerprint['type'] == 'phash':
                        items_by_phash[fingerprint['value']].append(item)

        for phash, phash_items in items_by_phash.items():
            if len(phash_items) > 1:
                _do_process_item_group(phash_items, get_item=get_item, update_item=update_item, merge_items=merge_items, setting_use_merge=setting_use_merge)
    else:
        _do_process_item_group(items, get_item=get_item, update_item=update_item, merge_items=merge_items, setting_use_merge=setting_use_merge)


def _do_process_item_group(items: list, 
        get_item : Callable[[str], dict], update_item : Callable[[dict], None], merge_items : Callable[[list[int], int], dict] = None, 
        setting_use_merge: bool = False
):
    log.debug(f"processing item group {[file['path'] for item in items for file in item['files']]}")

    items_full = []
    oldest_item = None
    for item in items:
        item_full = get_item(item['id'])
        if item_full is None:
            log.warning(f"item \"{[file['path'] for file in oldest_item['files']]}\" ({tem['id']}) disappeared from database")
            continue
        
        items_full.append(item_full)

        if oldest_item is None or oldest_item['created_at'] > item_full['created_at']:
            oldest_item = item_full
    
    changes = {}
    if 'title' in oldest_item and oldest_item['title'] and len(oldest_item['title']) > 0:
         changes["title"] = oldest_item['title']
    if 'code' in oldest_item and oldest_item['code'] and len(oldest_item['code']) > 0:
         changes["code"] = oldest_item['code']
    if 'date' in oldest_item and oldest_item['date'] and len(oldest_item['date']) > 0:
         changes["date"] = oldest_item['date']
    if 'urls' in oldest_item and oldest_item['urls'] and len(oldest_item['urls']) > 0:
         changes["urls"] = {"mode": "SET", "values": oldest_item['urls']}
    if 'details' in oldest_item and oldest_item['details'] and len(oldest_item['details']):
         changes["details"] = oldest_item['details']
    if 'director' in oldest_item and oldest_item['director'] and len(oldest_item['director']) > 0:
         changes["director"] = oldest_item['director']
    if 'photographer' in oldest_item and oldest_item['photographer'] and len(oldest_item['photographer']) > 0:
         changes["photographer"] = oldest_item['photographer']
    if 'rating100' in oldest_item and oldest_item['rating100']:
         changes["rating100"] = oldest_item['rating100']
    if 'o_counter' in oldest_item and oldest_item['o_counter']:
         changes["o_counter"] = oldest_item['o_counter']
    if 'studio' in oldest_item and oldest_item['studio'] is not None:
         changes["studio_id"] = oldest_item['studio']['id']
    if 'performers' in oldest_item and  len(oldest_item['performers']) > 0:
        changes["performer_ids"] = {"mode": "ADD", "ids": [t['id'] for t in oldest_item['performers']]}
    if 'tags' in oldest_item and  len(oldest_item['tags']) > 0:
        changes["tag_ids"] = {"mode": "ADD", "ids": [t['id'] for t in oldest_item['tags']]}
    if 'galleries' in oldest_item and  len(oldest_item['galleries']) > 0:
        changes["gallery_ids"] = {"mode": "ADD", "ids": [t['id'] for t in oldest_item['galleries']]}
    
    ids = []
    for item_full in items_full:
        if oldest_item['id'] == item_full['id']:
            continue
        elif item_full['organized']:
            continue
        
        # is merge, or has changes from source
        if setting_use_merge \
                or ("title" in changes and ('title' not in item_full or item_full['title'] != changes["title"])) \
                or ("code" in changes and ('code' not in item_full or item_full['code'] != changes["code"])) \
                or ("date" in changes and ('date' not in item_full or item_full['date'] != changes["date"])) \
                or ("urls" in changes and ('urls' not in item_full or item_full['urls'] != changes["urls"])) \
                or ("director" in changes and ('director' not in item_full or item_full['director'] != changes["director"])) \
                or ("photographer" in changes and ('photographer' not in item_full or item_full['photographer'] != changes["photographer"])) \
                or ("rating100" in changes and ('rating100' not in item_full or item_full['rating100'] != changes["rating100"])) \
                or ("o_counter" in changes and ('o_counter' not in item_full or item_full['o_counter'] != changes["o_counter"])) \
                or ("studio_id" in changes and ('studio' not in item_full or item_full['studio'] is None or str(item_full['studio']['id']) != changes["studio_id"])) \
                or ("performer_ids" in changes and ('performers' not in item_full or len(item_full['performers']) < len(changes["performer_ids"]["ids"]))) \
                or ("tag_ids" in changes and ('tags' not in item_full or len(item_full['tags']) < len(changes["tag_ids"]["ids"]))) \
                or ("gallery_ids" in changes and ('galleries' not in item_full or len(item_full['galleries']) < len(changes["gallery_ids"]["ids"]))) \
            :
            ids.append(item_full['id'])

    if len(ids) > 0:
        if setting_use_merge:
            log.info(f"mergining metadata into {[file['path'] for file in oldest_item['files']]} from item group {[file['path'] for item in items if oldest_item['id'] != item['id'] for file in item['files']]}")
            merge_items(ids, oldest_item['id'])
        else:
            changes["ids"] = ids
            log.info(f"migrating metadata from {[file['path'] for file in oldest_item['files']]} to item group {[file['path'] for item in items if oldest_item['id'] != item['id'] for file in item['files']]}")
            update_item(changes)


json_input = json.loads(sys.stdin.read())
FRAGMENT_SERVER = json_input["server_connection"]
stash = StashInterface(FRAGMENT_SERVER)
config = stash.get_configuration()
settings = {
    "scenesUsePhash": False,
    'scenesPathPrefix': '',
    "imagesUsePhash": False,
    "imagesPathPrefix": ""
}
if "ExtensionChangeDetector" in config["plugins"]:
    settings.update(config["plugins"]["ExtensionChangeDetector"])

if "mode" in json_input["args"]:
    PLUGIN_ARGS = json_input["args"]["mode"]
    if "processAllScenes" in PLUGIN_ARGS:
        processAllScenes()
    elif "processAllImages" in PLUGIN_ARGS:
        processAllImages()
