package main

import (
	"context"
	"embed"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/constant"
	"github.com/QuantumNous/new-api/controller"
	"github.com/QuantumNous/new-api/i18n"
	"github.com/QuantumNous/new-api/logger"
	"github.com/QuantumNous/new-api/middleware"
	"github.com/QuantumNous/new-api/model"
	"github.com/QuantumNous/new-api/relaykit/types"
	"github.com/QuantumNous/new-api/service"
	"github.com/QuantumNous/new-api/service/authz"
	"github.com/QuantumNous/new-api/setting/ratio_setting"
	"github.com/gin-gonic/gin"
	"github.com/joho/godotenv"
)

//go:embed ui/index.html ui/app.js ui/style.css
var uiFiles embed.FS

func main() {
	_ = godotenv.Load(".env")
	common.InitEnv()
	logger.SetupLogger()
	ratio_setting.InitRatioSettings()
	service.InitHttpClient()
	service.InitTokenEncoders()

	if err := model.InitDB(); err != nil {
		log.Fatalf("initialize database: %v", err)
	}
	if err := authz.Init(model.DB); err != nil {
		log.Fatalf("initialize authorization: %v", err)
	}
	if common.PasswordLoginEncryptionEnabled {
		if err := model.InitPasswordEncryption(); err != nil {
			log.Fatalf("initialize password encryption: %v", err)
		}
	}
	defer func() {
		if err := model.CloseDB(); err != nil {
			log.Printf("close database: %v", err)
		}
	}()
	model.CheckSetup()
	model.InitOptionMap()
	if err := model.InitLogDB(); err != nil {
		log.Fatalf("initialize log database: %v", err)
	}
	if err := common.InitRedisClient(); err != nil {
		log.Fatalf("initialize cache: %v", err)
	}
	if err := i18n.Init(); err != nil {
		log.Printf("initialize translations: %v", err)
	} else {
		i18n.SetUserLangLoader(model.GetUserLanguage)
	}
	if common.RedisEnabled {
		common.MemoryCacheEnabled = true
	}
	if common.MemoryCacheEnabled {
		model.InitChannelCache()
		go model.SyncChannelCache(common.SyncFrequency)
	}
	model.GetPricing()

	if os.Getenv("GIN_MODE") != "debug" {
		gin.SetMode(gin.ReleaseMode)
	}
	engine := gin.New()
	if err := middleware.ConfigureTrustedProxies(engine); err != nil {
		log.Fatalf("configure trusted proxies: %v", err)
	}
	engine.Use(gin.Recovery(), middleware.RequestId(), middleware.Version(), middleware.I18n())
	middleware.SetUpLogger(engine)
	engine.Use(middleware.CORS(), middleware.DecompressRequestMiddleware(), middleware.BodyStorageCleanup())
	engine.Use(func(c *gin.Context) {
		c.Header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
		c.Header("X-Content-Type-Options", "nosniff")
		c.Next()
	})
	engine.GET("/", func(c *gin.Context) {
		c.Header("Cache-Control", "no-store")
		data, _ := uiFiles.ReadFile("ui/index.html")
		c.Data(http.StatusOK, "text/html; charset=utf-8", data)
	})
	engine.GET("/app.js", func(c *gin.Context) {
		c.Header("Cache-Control", "no-store")
		data, _ := uiFiles.ReadFile("ui/app.js")
		c.Data(http.StatusOK, "text/javascript; charset=utf-8", data)
	})
	engine.GET("/style.css", func(c *gin.Context) {
		c.Header("Cache-Control", "no-store")
		data, _ := uiFiles.ReadFile("ui/style.css")
		c.Data(http.StatusOK, "text/css; charset=utf-8", data)
	})

	api := engine.Group("/api", middleware.RouteTag("api"), middleware.AccessTokenAudit(), middleware.GlobalAPIRateLimit())
	api.GET("/status", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})
	api.GET("/setup", controller.GetSetup)
	api.POST("/setup", middleware.CriticalRateLimit(), middleware.AnonymousRequestBodyLimit(), controller.PostSetup)
	api.GET("/user/login/encryption-key", middleware.DisableCache(), controller.GetPasswordEncryptionKey)
	api.POST("/user/login", middleware.CriticalRateLimit(), middleware.DisableCache(), middleware.AnonymousRequestBodyLimit(), middleware.TurnstileCheck(), controller.Login)
	api.POST("/user/login/2fa", middleware.CriticalRateLimit(), middleware.DisableCache(), middleware.AnonymousRequestBodyLimit(), controller.Verify2FALogin)
	api.POST("/user/auth/refresh", middleware.SessionCookieOriginGuard(), middleware.CriticalRateLimit(), middleware.DisableCache(), controller.RefreshAuth)
	api.POST("/user/auth/logout", middleware.SessionCookieOriginGuard(), middleware.CriticalRateLimit(), middleware.DisableCache(), controller.AuthLogout)
	api.GET("/user/self", middleware.UserAuth(), controller.GetSelf)

	channels := api.Group("/channel", middleware.AdminAuth())
	channels.GET("/", middleware.RequirePermission(authz.ChannelRead), controller.GetAllChannels)
	channels.GET("/default_base_urls", middleware.RequirePermission(authz.ChannelRead), controller.GetChannelDefaultBaseURLs)
	channels.POST("/fetch_models", middleware.CriticalRateLimit(), middleware.DisableCache(), middleware.RequirePermission(authz.ChannelSensitiveWrite), controller.FetchModels)
	channels.GET("/:id", middleware.RequirePermission(authz.ChannelRead), controller.GetChannel)
	channels.GET("/:id/fetch_models", middleware.RequirePermission(authz.ChannelOperate), controller.FetchUpstreamModels)
	channels.POST("/", middleware.RequirePermission(authz.ChannelSensitiveWrite), controller.AddChannel)
	channels.PUT("/", middleware.RequirePermission(authz.ChannelWrite), controller.UpdateChannel)
	channels.GET("/test/:id", middleware.RequirePermission(authz.ChannelOperate), controller.TestChannel)
	channels.POST("/:id/status", middleware.RequirePermission(authz.ChannelOperate), controller.UpdateChannelStatus)
	channels.DELETE("/:id", middleware.RequirePermission(authz.ChannelSensitiveWrite), controller.DeleteChannel)

	tokens := api.Group("/token", middleware.UserAuth(), middleware.TokenOperationAudit())
	tokens.GET("/", controller.GetAllTokens)
	tokens.POST("/", controller.AddToken)
	tokens.POST("/:id/key", middleware.CriticalRateLimit(), middleware.DisableCache(), controller.GetTokenKey)
	tokens.DELETE("/:id", controller.DeleteToken)
	models := engine.Group("/v1/models", middleware.RouteTag("relay"), middleware.TokenAuth())
	models.GET("", func(c *gin.Context) {
		controller.ListModels(c, constant.ChannelTypeOpenAI)
	})

	chat := engine.Group(
		"/v1",
		middleware.RouteTag("relay"),
		middleware.SystemPerformanceCheck(),
		middleware.TokenAuth(),
		middleware.ModelRequestRateLimit(),
		middleware.Distribute(),
	)
	chat.POST("/chat/completions", func(c *gin.Context) {
		controller.Relay(c, types.RelayFormatOpenAI)
	})

	port := os.Getenv("PORT")
	if port == "" {
		port = strconv.Itoa(*common.Port)
	}
	server := &http.Server{Addr: ":" + port, Handler: engine}
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("serve model API: %v", err)
		}
	}()
	log.Printf("New API model access started on port %s", port)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	if err := server.Shutdown(shutdownCtx); err != nil {
		log.Printf("shutdown model API: %v", err)
	}
}
