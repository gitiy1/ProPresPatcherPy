import unittest

from ota_probe import api_url, merge_query, parse_builds, same_build, version_key


class OtaProbeTests(unittest.TestCase):
    def test_api_url_uses_windows_upgrade_parameters(self):
        self.assertEqual(
            api_url(
                "https://api.example.test/v1.1",
                "/pro/upgrade",
                merge_query(
                    [
                        "platform=win32",
                        "osVersion=10.0",
                        "appVersion=0",
                        "buildNumber=0",
                        "includeNotes=0",
                        "format=",
                    ],
                    [],
                ),
            ),
            "https://api.example.test/v1.1/pro/upgrade?platform=win32&osVersion=10.0&appVersion=0&buildNumber=0&includeNotes=0&format=",
        )

    def test_query_override_replaces_current_build(self):
        query = merge_query(
            ["platform=win32", "appVersion=0", "buildNumber=0", "includeNotes=0"],
            ["buildNumber=352584193"],
        )
        self.assertEqual(query, ["platform=win32", "appVersion=0", "buildNumber=352584193", "includeNotes=0"])

    def test_parse_builds_filters_beta_and_unavailable(self):
        payload = {
            "BuildInformation": [
                {
                    "BuildNumber": 10,
                    "Version": "21.4.2",
                    "DownloadUrl": "https://downloads.example.test/pro-10.exe",
                    "IsBeta": False,
                    "IsAvailable": True,
                },
                {
                    "buildNumber": 11,
                    "version": "21.5.0-beta",
                    "downloadUrl": "https://downloads.example.test/pro-11.exe",
                    "isBeta": True,
                    "isAvailable": True,
                },
                {
                    "buildNumber": 12,
                    "version": "21.6.0",
                    "downloadUrl": "https://downloads.example.test/pro-12.exe",
                    "isBeta": False,
                    "isAvailable": False,
                },
            ]
        }
        builds = parse_builds(payload, "production", {"downloads.example.test"})
        self.assertEqual([build["build_number"] for build in builds], [10])

    def test_beta_selection_and_version_order(self):
        payload = {
            "upgrades": [
                {
                    "buildNumber": 30,
                    "version": "22.0.0-beta",
                    "downloadUrl": "https://downloads.example.test/pro-30.exe",
                    "isBeta": True,
                    "isAvailable": True,
                },
                {
                    "buildNumber": 29,
                    "version": "21.9.0",
                    "downloadUrl": "https://downloads.example.test/pro-29.exe",
                    "isBeta": False,
                    "isAvailable": True,
                },
            ]
        }
        builds = parse_builds(payload, "beta", {"downloads.example.test"})
        self.assertEqual(max(builds, key=version_key)["build_number"], 30)

    def test_same_build_ignores_url_rotation(self):
        previous = {"build_number": 10, "version": "21.4.2", "channel": "production", "download_url": "old"}
        current = {"build_number": 10, "version": "21.4.2", "channel": "production", "download_url": "new"}
        self.assertTrue(same_build(previous, current))


if __name__ == "__main__":
    unittest.main()
