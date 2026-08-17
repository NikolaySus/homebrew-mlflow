require "fileutils"
require "securerandom"

secret_dir = "/run/platform-secrets"
oauth_redirect_uri = ENV.fetch(
  "HOMEBREW_MLFLOW_GITLAB_OAUTH_REDIRECT_URI",
  "http://localhost:8080/api/v1/auth/web/callback"
)
client_id_path = File.join(secret_dir, "gitlab-oauth-client-id")
client_secret_path = File.join(secret_dir, "gitlab-oauth-client-secret")
token_path = File.join(secret_dir, "gitlab-integration-token")

FileUtils.mkdir_p(secret_dir, mode: 0o700)
FileUtils.chmod(0o711, secret_dir)

def write_secret(path, value)
  File.open(path, File::WRONLY | File::CREAT | File::TRUNC, 0o600) do |file|
    file.write(value)
  end
  FileUtils.chmod(0o444, path)
end

unless File.exist?(client_id_path) && File.exist?(client_secret_path)
  Doorkeeper::Application.where(name: "Homebrew MLflow").destroy_all
  application = Doorkeeper::Application.create!(
    name: "Homebrew MLflow",
    redirect_uri: oauth_redirect_uri,
    scopes: "openid profile read_user",
    organization_id: Organizations::Organization.find_by!(path: "default").id,
    confidential: true,
    trusted: true
  )
  write_secret(client_id_path, application.uid)
  write_secret(client_secret_path, application.plaintext_secret)
end

unless File.exist?(token_path)
  value = "glpat-#{SecureRandom.hex(24)}"
  token = User.find_by_username!("root").personal_access_tokens.create!(
    name: "Homebrew MLflow integration",
    scopes: ["api"],
    expires_at: 90.days.from_now
  )
  token.set_token(value)
  token.save!
  write_secret(token_path, value)
end
