# The Homebrew formula (docs/packaging.md, "Homebrew"): the same tree the
# .deb installs, under the Cellar, with the smoking-pi command on PATH.
# Docker Desktop is the runtime, and on macOS Pro's host-network features
# (the CPE hop, Wi-Fi stats) measure Docker's Linux VM, not the Mac.
#
#   brew tap estcarisimo/smoking-pi https://github.com/estcarisimo/smoking-pi
#   brew install smoking-pi
#
# `packaging/homebrew/bump.sh vX.Y.Z` updates url and sha256 after a
# release (the tarball's checksum cannot be known before the tag exists).
class SmokingPi < Formula
  desc "SmokePing network monitoring stack, from a Raspberry Pi to any Docker host"
  homepage "https://github.com/estcarisimo/smoking-pi"
  url "https://github.com/estcarisimo/smoking-pi/archive/refs/tags/v2.11.0.tar.gz"
  sha256 "c324e9b15269213e6e24dd3ea4179dcbdda5fd4313b119192a3c49a53d5ba344"
  license "MIT"
  head "https://github.com/estcarisimo/smoking-pi.git", branch: "main"

  # macOS ships bash 3.2, BSD sed and BSD readlink; the scripts need bash
  # >= 4.4, `sed -i` and `readlink -f` as GNU spells them. The wrapper puts
  # these first on PATH; the shebangs are rewritten below.
  depends_on "bash"
  depends_on "coreutils"
  depends_on "gnu-sed"
  # The instrumentation doctor (`smoking-pi doctor`) needs PyYAML; the
  # command looks for shared/modules/doctor/.venv first.
  depends_on "python@3.12"

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/54/ed/79a089b6be93607fa5cdaedf301d7dfb23af5f25c398d5ead2525b063e17/pyyaml-6.0.2.tar.gz"
    sha256 "d584d9ec91ad65861cc08d42e834324ef890a082e591037abe114850ff7bbc3e"
  end

  def install
    # The whole tree: the command runs everything relative to its home.
    libexec.install Dir["*"]
    # /bin/bash is 3.2 on macOS whatever PATH says; every script says
    # `#!/bin/bash`, so point them at whichever bash PATH finds (the wrapper
    # puts Homebrew's first).
    inreplace Dir[libexec/"**/*.sh"] + [libexec/"packaging/smoking-pi"],
              %r{\A#!/bin/bash}, "#!/usr/bin/env bash", false

    venv = libexec/"shared/modules/doctor/.venv"
    system Formula["python@3.12"].opt_bin/"python3.12", "-m", "venv", venv
    resource("pyyaml").stage do
      system venv/"bin/pip", "install", "--no-deps", "--no-build-isolation", "."
    end

    (bin/"smoking-pi").write_env_script libexec/"packaging/smoking-pi",
      PATH: "#{Formula["coreutils"].opt_libexec}/gnubin:#{Formula["gnu-sed"].opt_libexec}/gnubin:#{Formula["bash"].opt_bin}:$PATH",
      SMOKING_PI_HOME: libexec
  end

  # `brew services start smoking-pi` runs `smoking-pi up` once at login;
  # Docker's own restart policy keeps the containers up after that.
  service do
    run [opt_bin/"smoking-pi", "up"]
    keep_alive false
    log_path var/"log/smoking-pi.log"
    error_log_path var/"log/smoking-pi.log"
  end

  def caveats
    <<~EOS
      Needs Docker Desktop (running), then:
        smoking-pi install          # edition, backend, optional services; starts the stack
        brew services start smoking-pi   # start it at login

      On macOS, Pro's host-network measurements (the CPE hop, Wi-Fi stats)
      see Docker's Linux VM, not this Mac. Basic and Standard are unaffected.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/smoking-pi version")
    assert_match "home:", shell_output("#{bin}/smoking-pi paths")
  end
end
