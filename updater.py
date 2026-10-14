"""
IO PDX Mesh Python module.
This is designed to allow tools to check if they are out of date or not and supply a download link to the latest.

author : ross-g
"""

import json
import logging
import re
import time
from datetime import date, datetime
from os.path import splitext

# Py2, Py3 compatibility
try:
    from urllib.request import Request, URLError, urlopen
except ImportError:
    from urllib2 import Request, URLError, urlopen  # type: ignore

from . import IO_PDX_INFO, IO_PDX_SETTINGS

UPDATER_LOG = logging.getLogger("io_pdx.updater")


""" ====================================================================================================================
    Helper functions.
========================================================================================================================
"""


class Github_API(object):
    """
    Handles connection to Githubs API to get some data on releases for this repository.
    """

    API_URL = "https://api.github.com"

    def __init__(self, owner, repo):
        self.api = self.API_URL
        self.owner = owner
        self.repo = repo
        self.args = {"owner": self.owner, "repo": self.repo, "api": self.api}

        self.AT_LATEST = False
        # 更新检查是否真的成功过：失败时不能拿它当「有新版本」处理。
        self.UPDATE_KNOWN = False
        self.LATEST_VERSION = 0.0
        self.LATEST_RELEASE = "https://github.com/{owner}/{repo}/releases/latest".format(**self.args)
        self.LATEST_NOTES = ""
        self.LATEST_URL = ""
        self.CURRENT_VERSION = IO_PDX_INFO["current_git_tag"]
        self.refresh()

    @staticmethod
    def get_data(url, time=1.0):
        req = Request(url)
        result = urlopen(req, timeout=time)
        result_str = result.read()
        result.close()

        return json.JSONDecoder().decode(result_str.decode())

    @staticmethod
    def _version_tuple(value):
        """把版本号转成可比较的整数元组，容忍 "v1.0"、"0.99.1" 这类写法。

        tag 不保证是能直接 float() 的数字（上游换成 v 前缀或三段版本号就崩），
        缓存里读回来的也可能是字符串，统一在这里归一，解析不出来返回 None。
        """
        if value is None:
            return None
        numbers = re.findall(r"\d+", str(value))
        return tuple(int(n) for n in numbers) if numbers else None

    def refresh(self, force=False):
        recheck = True

        # only check for updates once per day
        last_check_date = IO_PDX_SETTINGS.last_update_check
        if last_check_date is not None:
            try:
                recheck = date.today() > datetime.strptime(str(last_check_date), "%Y-%m-%d").date()
            except ValueError:
                # 配置文件里是旧格式或手改过的日期，当作需要重新检查
                recheck = True

        if recheck or force:
            start = time.time()

            # get latest release data
            releases_url = "{api}/repos/{owner}/{repo}/releases".format(**self.args)

            latest = None
            try:
                release_list = self.get_data(releases_url)
                latest = release_list[0] if release_list else None
            except URLError as err:
                UPDATER_LOG.warning("Unable to check for update. ({})".format(err.reason))
            except Exception as err:
                UPDATER_LOG.error("Failed during update check. ({})".format(err))

            if latest is None:
                # 没拿到发布数据（网络失败或仓库里一个 release 都没有）：
                # 保持 UPDATE_KNOWN 为假，界面按「检查失败」处理而不是谎报有新版本。
                UPDATER_LOG.warning("Found no releases during update check.")
                self.AT_LATEST = False
                return

            try:
                # store data
                self.LATEST_VERSION = float(latest["tag_name"])
            except (KeyError, TypeError, ValueError):
                self.LATEST_VERSION = latest.get("tag_name") if isinstance(latest, dict) else None
            try:
                self.LATEST_URL = {
                    splitext(asset["name"])[0].split("-")[0]: asset["browser_download_url"]
                    for asset in latest["assets"]
                }
            except (KeyError, TypeError):
                self.LATEST_URL = {}
            # 这里是下载页 URL；资产字典在 LATEST_URL 里，两者不能混用
            self.LATEST_RELEASE = latest.get("html_url") or self.LATEST_RELEASE
            try:
                self.LATEST_NOTES = "{0}\r\nRelease version: {1}\r\n{2}".format(
                    latest["published_at"].split("T")[0], latest["tag_name"], latest["body"]
                )
            except (KeyError, TypeError, AttributeError):
                self.LATEST_NOTES = ""

            # cache data to settings
            IO_PDX_SETTINGS.github_latest_version = self.LATEST_VERSION
            IO_PDX_SETTINGS.github_latest_url = self.LATEST_URL
            IO_PDX_SETTINGS.github_latest_notes = self.LATEST_NOTES

            IO_PDX_SETTINGS.last_update_check = str(date.today())
            UPDATER_LOG.info("Checked for update. ({0:.4f} sec)".format(time.time() - start))

        else:
            # used cached release data in settings
            self.LATEST_VERSION = IO_PDX_SETTINGS.github_latest_version
            self.LATEST_URL = IO_PDX_SETTINGS.github_latest_url
            self.LATEST_NOTES = IO_PDX_SETTINGS.github_latest_notes
            if self.LATEST_URL and not isinstance(self.LATEST_URL, dict):
                self.LATEST_URL = {}

            UPDATER_LOG.info("Skipped update check. (already ran today)")

        current = self._version_tuple(self.CURRENT_VERSION)
        latest = self._version_tuple(self.LATEST_VERSION)
        self.UPDATE_KNOWN = current is not None and latest is not None
        # 本地比线上新时不该提示更新，所以用 >= 而不是 ==
        self.AT_LATEST = not self.UPDATE_KNOWN or current >= latest


github = Github_API(owner=IO_PDX_INFO["maintainer"], repo=IO_PDX_INFO["id"])
