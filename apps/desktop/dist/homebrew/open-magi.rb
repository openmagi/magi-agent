# Homebrew cask for the "Open Magi" desktop app (macOS arm64).
#
# This file is the SOURCE OF TRUTH; copy it into the tap repo
# `openmagi/homebrew-tap` under `Casks/open-magi.rb` to publish. Users then:
#
#   brew install --cask openmagi/tap/open-magi
#
# After the FIRST release, replace `sha256 :no_check` with the real digest of
# the published dmg (see dist/homebrew/README.md). `version` and the tag/url
# template stay in sync with the `desktop-v<version>` release tag.
cask "open-magi" do
  version "0.1.0"
  sha256 :no_check

  url "https://github.com/openmagi/magi-agent/releases/download/desktop-v#{version}/Open-Magi_#{version}_aarch64.dmg",
      verified: "github.com/openmagi/magi-agent/"
  name "Open Magi"
  desc "Self-host Open Magi as a local desktop app"
  homepage "https://openmagi.ai/"

  # arm64-only for the first release; minimumSystemVersion is 11.0 (Big Sur).
  depends_on arch: :arm64
  depends_on macos: ">= :big_sur"

  app "Open Magi.app"

  # `~/.magi` holds the user's local agent data: memory, config, and keys.
  # This zap is intentionally COMMENTED OUT so a plain uninstall never deletes
  # it. Uncomment only if you want `brew uninstall --zap open-magi` to wipe all
  # local Open Magi data.
  # zap trash: "~/.magi"
end
