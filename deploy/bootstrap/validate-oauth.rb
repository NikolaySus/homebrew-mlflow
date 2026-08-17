expected_redirect_uri = ENV.fetch("HOMEBREW_MLFLOW_GITLAB_OAUTH_REDIRECT_URI")
application = Doorkeeper::Application.find_by(name: "Homebrew MLflow")

abort "GitLab OAuth application is missing" unless application
unless application.redirect_uri.split.include?(expected_redirect_uri)
  abort "GitLab OAuth application redirect URI does not match production"
end

puts "GitLab OAuth application redirect validated"
