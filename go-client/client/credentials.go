package client

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var deploymentPattern = regexp.MustCompile(`^[a-f0-9]{32}$`)

func ConfigDirectory() string {
	root := os.Getenv("XDG_CONFIG_HOME")
	if root == "" {
		home, _ := os.UserHomeDir()
		root = filepath.Join(home, ".config")
	}
	return filepath.Join(root, "uart-gateway")
}
func exists(path string) bool { _, e := os.Lstat(path); return e == nil || !os.IsNotExist(e) }
func profileDir(root, name string) (string, error) {
	if !ValidID(name) {
		return "", errors.New("連線名稱不合法")
	}
	return filepath.Join(root, "connections", name), nil
}
func DefaultName(root string) (string, error) {
	path := filepath.Join(root, "default-connection")
	if !exists(path) {
		return "default", nil
	}
	b, e := ReadPrivate(path, 128)
	if e != nil {
		return "", e
	}
	s := strings.TrimSpace(string(b))
	if !ValidID(s) {
		return "", errors.New("預設連線名稱不合法")
	}
	return s, nil
}
func readProfile(root, name string) (Object, error) {
	dir, e := profileDir(root, name)
	if e != nil {
		return nil, e
	}
	info, e := os.Lstat(dir)
	if e != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("連線目錄不存在或不安全，請由 Windows 設定 Client")
	}
	raw, e := ReadPrivate(filepath.Join(dir, "client.json"), 16384)
	if e != nil {
		return nil, e
	}
	v, e := StrictJSON(raw, 16)
	if e != nil {
		return nil, e
	}
	for k := range v {
		switch k {
		case "version", "deployment_id", "host", "port", "credentials", "gateway_config":
		default:
			return nil, errors.New("連線設定包含未知欄位")
		}
	}
	if len(v) < 5 {
		return nil, invalid()
	}
	if _, e = Integer(v, "version", 1, 1); e != nil {
		return nil, e
	}
	if !deploymentPattern.MatchString(String(v, "deployment_id")) {
		return nil, invalid()
	}
	if String(v, "host") != "127.0.0.1" && String(v, "host") != "::1" {
		return nil, invalid()
	}
	if _, e = Integer(v, "port", 1, 65535); e != nil {
		return nil, e
	}
	refs := Map(v, "credentials")
	if len(refs) == 0 || len(refs) > 3 {
		return nil, invalid()
	}
	for role, ref := range refs {
		s, ok := ref.(string)
		if !ok || len(s) > 4096 || role != "read" && role != "manage" && role != "tx" || !strings.HasPrefix(s, "file:") && !strings.HasPrefix(s, "env:") {
			return nil, invalid()
		}
	}
	return v, nil
}

type ConnectionOptions struct {
	Root      string
	Name      string
	Access    string
	Host      string
	Port      int
	TokenFile string
	TokenEnv  string
}
type Settings struct {
	Host       string
	Port       int
	Access     string
	Name       string
	Deployment string
	Root       string
	reference  string
}

func (o ConnectionOptions) Resolve() (Settings, error) {
	root := o.Root
	if root == "" {
		root = ConfigDirectory()
	}
	root, e := filepath.Abs(root)
	if e != nil {
		return Settings{}, e
	}
	s := Settings{Root: root, Host: o.Host, Port: o.Port}
	if s.Host == "" {
		s.Host = "127.0.0.1"
	}
	if s.Port == 0 {
		s.Port = 18081
	}
	if o.Access != "" && o.Access != "read" && o.Access != "manage" && o.Access != "tx" {
		return s, errors.New("角色僅接受 read、manage 或 tx")
	}
	if o.TokenFile != "" || o.TokenEnv != "" {
		if o.Name != "" || o.Access != "" || o.TokenFile != "" && o.TokenEnv != "" {
			return s, errors.New("明確 Token 來源與固定連線／角色不可混用")
		}
		if o.TokenFile != "" {
			s.reference = "file:" + o.TokenFile
		} else {
			s.reference = "env:" + o.TokenEnv
		}
		return s, nil
	}
	name := o.Name
	if name == "" {
		name, e = DefaultName(root)
		if e != nil {
			return s, e
		}
	}
	dir, e := profileDir(root, name)
	if e != nil {
		return s, e
	}
	configured := exists(dir) || exists(filepath.Join(root, "default-connection"))
	if !configured && o.Name == "" && o.Access == "" && os.Getenv("UART_GATEWAY_TOKEN") != "" {
		s.reference = "env:UART_GATEWAY_TOKEN"
		return s, nil
	}
	if !configured {
		return s, errors.New("此環境尚未設定連線，請在 Windows 執行 uart setup 安裝此 Client 與原角色憑證")
	}
	v, e := readProfile(root, name)
	if e != nil {
		return s, e
	}
	s.Name = name
	s.Access = o.Access
	if s.Access == "" {
		s.Access = "read"
	}
	ref, ok := Map(v, "credentials")[s.Access].(string)
	if !ok {
		return s, errors.New("此連線未配置所選角色，不會改用其他憑證")
	}
	if strings.HasPrefix(ref, "file:") {
		p := strings.TrimPrefix(ref, "file:")
		if !filepath.IsAbs(p) {
			p = filepath.Join(dir, p)
		}
		ref = "file:" + p
	}
	s.reference = ref
	s.Deployment = String(v, "deployment_id")
	if o.Host == "" {
		s.Host = String(v, "host")
	}
	if o.Port == 0 {
		s.Port, _ = Integer(v, "port", 1, 65535)
	}
	return s, nil
}
func (s Settings) Connect(ctx context.Context) (*Client, error) {
	var token string
	var e error
	if strings.HasPrefix(s.reference, "file:") {
		var raw []byte
		raw, e = ReadPrivate(strings.TrimPrefix(s.reference, "file:"), 1024)
		token = strings.TrimSpace(string(raw))
	} else {
		token = strings.TrimSpace(os.Getenv(strings.TrimPrefix(s.reference, "env:")))
	}
	if e != nil {
		return nil, e
	}
	if !tokenPattern.MatchString(token) {
		return nil, errors.New("所選憑證來源不存在或格式不合法")
	}
	return Connect(ctx, s.Host, s.Port, token, s.Access)
}
func ImportCredential(root, name string, port int, raw []byte) error {
	if len(raw) > 16384 {
		return errors.New("憑證傳輸包超過上限")
	}
	v, e := StrictJSON(raw, 16)
	if e != nil {
		return errors.New("憑證包格式不合法，未顯示內容")
	}
	if len(v) != 5 {
		return invalid()
	}
	if _, e = Integer(v, "version", 1, 1); e != nil {
		return e
	}
	deployment := String(v, "deployment_id")
	role := String(v, "access")
	token := String(v, "token")
	if !deploymentPattern.MatchString(deployment) || !tokenPattern.MatchString(token) || role != "read" && role != "manage" && role != "tx" {
		return errors.New("憑證包欄位不合法")
	}
	for k := range v {
		switch k {
		case "version", "deployment_id", "access", "token", "port":
		default:
			return invalid()
		}
	}
	number, e := Integer(v, "port", 1, 65535)
	if e != nil {
		return e
	}
	if port != 0 {
		number = port
	}
	if number < 1 || number > 65535 {
		return invalid()
	}
	dir, e := profileDir(root, name)
	if e != nil {
		return e
	}
	var profile Object
	if exists(dir) {
		profile, e = readProfile(root, name)
		if e != nil {
			return e
		}
		old, _ := Integer(profile, "port", 1, 65535)
		if String(profile, "deployment_id") != deployment || old != number {
			return errors.New("既有連線屬於不同部署或 Port，未覆蓋")
		}
		if ref, ok := Map(profile, "credentials")[role].(string); ok {
			p := strings.TrimPrefix(ref, "file:")
			if !strings.HasPrefix(ref, "file:") {
				return errors.New("既有角色使用其他憑證來源，未覆蓋")
			}
			if !filepath.IsAbs(p) {
				p = filepath.Join(dir, p)
			}
			prior, e := ReadPrivate(p, 1024)
			if e != nil {
				return e
			}
			if subtle.ConstantTimeCompare([]byte(strings.TrimSpace(string(prior))), []byte(token)) != 1 {
				return errors.New("既有角色憑證不同，未覆蓋")
			}
			return nil
		}
	} else {
		profile = Object{"version": 1, "deployment_id": deployment, "host": "127.0.0.1", "port": number, "credentials": Object{}}
	}
	if e = PrivateDir(dir); e != nil {
		return e
	}
	path := filepath.Join(dir, role+".secret")
	if e = WritePrivate(path, []byte(token+"\n"), false); e != nil {
		return e
	}
	Map(profile, "credentials")[role] = "file:" + role + ".secret"
	data, _ := json.Marshal(profile)
	if e = WritePrivate(filepath.Join(dir, "client.json"), append(data, '\n'), exists(filepath.Join(dir, "client.json"))); e != nil {
		os.Remove(path)
		return e
	}
	defaultPath := filepath.Join(root, "default-connection")
	if !exists(defaultPath) {
		return WritePrivate(defaultPath, []byte(name+"\n"), false)
	}
	return nil
}
func SelectDefault(root, name string) error {
	if _, e := readProfile(root, name); e != nil {
		return e
	}
	return WritePrivate(filepath.Join(root, "default-connection"), []byte(name+"\n"), true)
}
