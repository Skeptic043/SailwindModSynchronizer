STEAM_APP_ID = "1764530"
GAME_EXE_NAME = "Sailwind.exe"
DEFAULT_GAME_PATH = r"D:\SteamLibrary\steamapps\common\Sailwind"

APP_NAME = "Sailwind Mod Synchronizer"
APP_VERSION = "0.4.0"
APP_REPO = "https://github.com/foxyv/SailwindModSynchronizer"
APP_OWNER = "foxyv"
APP_REPO_NAME = "SailwindModSynchronizer"
APP_CATALOG_BRANCH = "master"
APP_CATALOG_MODLIST_PATH = "catalog/ModList.json"
APP_CATALOG_VERSIONS_PATH = "catalog/release_versions.json"
GITHUB_NEW_TOKEN_URL = (
    "https://github.com/settings/tokens/new?description=Sailwind+Mod+Synchronizer"
)
USER_AGENT = f"SailwindModSynchronizer/{APP_VERSION}"
UPDATE_CHECK_HOURS = 24

DEFAULT_BEPINEX_VERSION = "5.4.2305"
BEPINEX_NAMESPACE = "BepInEx"
BEPINEX_PACK_NAME = "BepInExPack"

MVC_OWNER = "bryon82"
MVC_REPO = "SailwindModVersionChecker"
MVC_BRANCH = "main"

JSDELIVR_MODLIST = (
    f"https://cdn.jsdelivr.net/gh/{MVC_OWNER}/{MVC_REPO}@{MVC_BRANCH}/ModList.json"
)
JSDELIVR_VERSIONS = (
    f"https://cdn.jsdelivr.net/gh/{MVC_OWNER}/{MVC_REPO}@{MVC_BRANCH}/release_versions.json"
)
GITHUB_RAW_MODLIST = (
    f"https://raw.githubusercontent.com/{MVC_OWNER}/{MVC_REPO}/{MVC_BRANCH}/ModList.json"
)
GITHUB_RAW_VERSIONS = (
    f"https://raw.githubusercontent.com/{MVC_OWNER}/{MVC_REPO}/{MVC_BRANCH}/release_versions.json"
)

JSDELIVR_APP_MODLIST = (
    f"https://cdn.jsdelivr.net/gh/{APP_OWNER}/{APP_REPO_NAME}@{APP_CATALOG_BRANCH}/{APP_CATALOG_MODLIST_PATH}"
)
JSDELIVR_APP_VERSIONS = (
    f"https://cdn.jsdelivr.net/gh/{APP_OWNER}/{APP_REPO_NAME}@{APP_CATALOG_BRANCH}/{APP_CATALOG_VERSIONS_PATH}"
)
GITHUB_RAW_APP_MODLIST = (
    f"https://raw.githubusercontent.com/{APP_OWNER}/{APP_REPO_NAME}/{APP_CATALOG_BRANCH}/{APP_CATALOG_MODLIST_PATH}"
)
GITHUB_RAW_APP_VERSIONS = (
    f"https://raw.githubusercontent.com/{APP_OWNER}/{APP_REPO_NAME}/{APP_CATALOG_BRANCH}/{APP_CATALOG_VERSIONS_PATH}"
)

THUNDERSTORE_PACK_API = (
    f"https://thunderstore.io/api/experimental/package/{BEPINEX_NAMESPACE}/{BEPINEX_PACK_NAME}/"
)
THUNDERSTORE_DOWNLOAD = (
    "https://thunderstore.io/package/download/{namespace}/{name}/{version}/"
)

CONFIG_FILENAME = "config.json"
DEFAULT_PACK_NAME = "Default"
