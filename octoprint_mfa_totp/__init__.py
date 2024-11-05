import json
import os
import time
from typing import Dict, Optional

import octoprint.plugin
import pyotp
from flask import abort, jsonify, make_response
from flask_babel import gettext
from flask_login import current_user
from octoprint.plugin.types import WrongMfaCredentials
from octoprint.schema import BaseModel
from octoprint.server.util.flask import ensure_credentials_checked_recently

CLEANUP_CUTOFF = 60 * 30  # 30 minutes
VALID_WINDOW = 1  # delay of one tick is ok


class MfaTotpUserSettings(BaseModel):
    created: int
    secret: str
    last_used: Optional[str] = None
    active: bool = False


class MfaTotpSettings(BaseModel):
    users: Dict[str, MfaTotpUserSettings] = {}


class MfaTotpPlugin(
    octoprint.plugin.AssetPlugin,
    octoprint.plugin.MfaPlugin,
    octoprint.plugin.SettingsPlugin,
    octoprint.plugin.SimpleApiPlugin,
    octoprint.plugin.TemplatePlugin,
):
    def __init__(self):
        self._data = None

    def initialize(self):
        self._load_data()

    @property
    def _data_file(self):
        return os.path.join(self.get_plugin_data_folder(), "mfa_totp_data.json")

    def _load_data(self):
        if not os.path.exists(self._data_file):
            self._data = MfaTotpSettings()
        else:
            try:
                with open(self._data_file) as f:
                    data = json.load(f)
                self._data = MfaTotpSettings.model_validate(data)
            except Exception as e:
                self._logger.exception(f"Error loading TOTP MFA data: {e}")

        if self._cleanup_data():
            self._save_data()

    def _save_data(self):
        self._cleanup_data()
        try:
            with open(self._data_file, "w") as f:
                f.write(self._data.model_dump_json(indent=4))
        except Exception as e:
            self._logger.exception(f"Error saving TOTP MFA data: {e}")

    def _cleanup_data(self):
        now = time.time()
        dirty = False
        for userid, user in list(self._data.users.items()):
            if not user.active and user.created < now - CLEANUP_CUTOFF:
                self._data.users.pop(userid)
                dirty = True
        return dirty

    def _enroll_user(self, userid):
        if userid in self._data.users and self._data.users[userid].active:
            raise ValueError("User already enrolled")

        if userid in self._data.users:
            secret = self._data.users[userid].secret
        else:
            secret = pyotp.random_base32()
            self._data.users[userid] = MfaTotpUserSettings(
                created=int(time.time()), secret=secret
            )
            self._save_data()

        return secret, self._provisioning_uri(userid)

    def _provisioning_uri(self, userid):
        if userid not in self._data.users:
            raise ValueError("User not enrolled")

        secret = self._data.users[userid].secret
        return pyotp.totp.TOTP(secret).provisioning_uri(
            name=userid, issuer_name="OctoPrint"
        )

    def _verify_user(self, userid, token):
        if userid not in self._data.users:
            return False

        if self._data.users[userid].last_used == token:
            # prevent replay attacks
            return False

        secret = self._data.users[userid].secret
        if pyotp.TOTP(secret).verify(token, valid_window=VALID_WINDOW):
            self._data.users[userid].last_used = token
            self._save_data()
            return True

    ##~~ AssetPlugin mixin

    def get_assets(self):
        return {
            "js": ["js/mfa_totp.js"],
            "clientjs": ["clientjs/mfa_totp.js"],
        }

    ##~~ TemplatePlugin mixin

    def get_template_configs(self):
        return [
            {
                "type": "usersettings_mfa",
                "name": gettext("TOTP"),
            },
            {
                "type": "mfa_login",
                "name": gettext("TOTP"),
            },
        ]

    def is_template_autoescaped(self):
        return True

    ##~~ SimpleApiPlugin mixin

    def on_api_get(self, request):
        userid = current_user.get_id()
        return jsonify(
            active=userid in self._data.users and self._data.users[userid].active
        )

    def get_api_commands(self):
        return {"enroll": [], "activate": ["token"], "deactivate": []}

    def on_api_command(self, command, data):
        user = current_user
        if not user or not user.is_authenticated or not user.is_active:
            return abort(403)
        ensure_credentials_checked_recently()

        userid = user.get_id()

        if command == "enroll":
            # user enrollment: generate secret and return provisioning URI
            if userid in self._data.users and self._data.users[userid].active:
                return abort(409, "User already enrolled")

            key, uri = self._enroll_user(userid)
            return jsonify(key=key, uri=uri)

        elif command == "activate":
            # activate user: verify token, only then activate user
            if userid not in self._data.users:
                return abort(404, "User not enrolled")
            if self._data.users[userid].active:
                return abort(409, "User enrollment already verified")

            token = data.get("token", "")
            response = self.get_verification_response(userid, token)
            if response:
                return response

            self._data.users[userid].active = True
            self._save_data()
            return jsonify()

        elif command == "deactivate":
            # deactivate user: verify token, only then deactivate user
            if userid not in self._data.users:
                return abort(404, "User not enrolled")

            if not self._data.users[userid].active:
                return abort(400, "User erollment is not active")

            token = data.get("token", "")
            response = self.get_verification_response(userid, token)
            if response:
                return response

            self._data.users.pop(userid)
            self._save_data()
            return jsonify(True)

    ##~~ MfaPlugin mixin

    def is_mfa_enabled(self, user, *args, **kwargs):
        userid = user.get_id()
        return userid in self._data.users and self._data.users[userid].active

    def has_mfa_credentials(self, request, user, data, *args, **kwargs):
        if not self.is_mfa_enabled(user):
            # this should never happen as the calling code should check if we are enabled
            # already, but still, if it does, we don't want to block the user
            return True

        token = data.get(f"mfa-{self._identifier}-token", "")
        if not token:
            # token not there? we need to ask for it
            return False

        userid = user.get_id()
        if not self._verify_user(userid, token):
            if token == self._data.users[userid].last_used:
                raise WrongMfaCredentials(
                    gettext(
                        "The entered token has already been used, please wait for a new one"
                    )
                )
            else:
                raise WrongMfaCredentials(gettext("Invalid token"))

        return True

    ##~~ Softwareupdate hook

    def get_update_information(self):
        return {
            "mfa_totp": {
                "displayName": self._plugin_name,
                "displayVersion": self._plugin_version,
                # version check: github repository
                "type": "github_release",
                "user": "OctoPrint",
                "repo": "OctoPrint-MfaTotp",
                "current": self._plugin_version,
                "stable_branch": {
                    "name": "Stable",
                    "branch": "main",
                    "commitish": ["devel", "main"],
                },
                "prerelease_branches": [
                    {
                        "name": "Prerelease",
                        "branch": "devel",
                        "commitish": ["devel", "main"],
                    }
                ],
                # update method: pip
                "pip": "https://github.com/OctoPrint/OctoPrint-MfaTotp/archive/{target_version}.zip",
            }
        }

    ##~~ Helpers

    def get_verification_response(self, userid, token):
        if not self._verify_user(userid, token):
            if token == self._data.users[userid].last_used:
                return make_response(
                    jsonify(
                        error="Token already used",
                        mfa_error=gettext(
                            "The entered token has already been used, please wait for a new one"
                        ),
                    ),
                    403,
                )
            else:
                return abort(403, "Invalid token")


__plugin_name__ = gettext("TOTP 2FA Plugin")
__plugin_pythoncompat__ = ">=3.7,<4"
__plugin_implementation__ = MfaTotpPlugin()
__plugin_hooks__ = {
    "octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
}
