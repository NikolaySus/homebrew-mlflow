expected_redirect_uri = ENV.fetch("HOMEBREW_MLFLOW_GITLAB_OAUTH_REDIRECT_URI")
application = Doorkeeper::Application.find_by(name: "Homebrew MLflow")

abort "GitLab OAuth application is missing" unless application
unless application.redirect_uri.split.include?(expected_redirect_uri)
  abort "GitLab OAuth application redirect URI does not match production"
end

puts "GitLab OAuth application redirect validated"

device_application = Doorkeeper::Application.find_by(name: "Homebrew MLflow Device")
abort "GitLab device OAuth application is missing" unless device_application
abort "GitLab device OAuth application must be public" if device_application.confidential?
unless device_application.device_code_enabled?
  abort "GitLab device OAuth application does not allow the device-code grant"
end
unless device_application.scopes.include?("read_user")
  abort "GitLab device OAuth application is missing the read_user scope"
end

puts "GitLab device OAuth application validated"
