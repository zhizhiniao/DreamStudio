Source: https://github.com/dreammis/social-auto-upload
Commit: 0012d2c355f88f683cc38dde2a2db209e14091bc
License: MIT, see LICENSE.
Local Douyin changes: single submit hook; removed implicit AI declaration and third-party switch.
Tencent overrides in runner.py disable implicit originality claims and require a confirmed management URL.
Xiaohongshu module added from the same commit; original SHA256 f4885e1d483a61221bde43abaa3aa6ccf1251aa7c6f953098324a03a919f693c.
Local xiaohongshu.py overrides upload, covers, source declaration and publish: bounded waits,
no implicit originality/geolocation, one publish click, exact success page plus visible confirmation.
The vendor's retrying video/note publish loops are not used by this service.

Kuaishou module added from the same commit; SHA256 fc97d1f83aa0d531284365d2e54a6e043bef420f8b8088b3e90b9ed785326c66.
Local kuaishou.py uses its file validation and cover controls only; login uses a shared persistent profile.
Video uploading, reviewed text, declarations and single-attempt submission are locally bounded overrides.
The vendor's login, cookie check, DOM-removal guide handling and retrying publish loops are never called.
