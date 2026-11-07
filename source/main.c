#include <3ds.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <malloc.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>

#define SOC_ALIGN       0x1000
#define SOC_BUFFERSIZE  0x100000
#define DEFAULT_PORT 8888
#define DEFAULT_SERVER_IP "192.168.1.1"
#define CONFIG_FILE "config.ini"
#define MAX_LINE 256
#define LCD_TOGGLE_KEY KEY_SELECT

typedef struct {
    u32 buttons;
    circlePosition circlepad;
    circlePosition cstick;
} controllerstate;

typedef struct {
    char serverip[64];
    int port;
    u32 lcdtogglekey;
} config;

void readconfigfile(config *cfg) {
    strcpy(cfg->serverip, DEFAULT_SERVER_IP);
    cfg->port = DEFAULT_PORT;
    cfg->lcdtogglekey = LCD_TOGGLE_KEY;
    
    FILE *file = fopen(CONFIG_FILE, "r");
    if (!file) {
        file = fopen(CONFIG_FILE, "w");
        if (file) {
            fprintf(file, "serverip=%s\n", DEFAULT_SERVER_IP);
            fprintf(file, "port=%d\n", DEFAULT_PORT);
            fprintf(file, "lcdtogglekey=KEY_SELECT\n");
            fclose(file);
        }
        return;
    }
    
    char line[MAX_LINE];
    while (fgets(line, MAX_LINE, file)) {
        size_t len = strlen(line);
        if (len > 0 && (line[len-1] == '\n' || line[len-1] == '\r')) line[len-1] = '\0';
        if (len > 1 && (line[len-2] == '\r')) line[len-2] = '\0';
        
        char *delimiter = strchr(line, '=');
        if (delimiter) {
            *delimiter = '\0';
            char *key = line;
            char *value = delimiter + 1;
            
            if (strcmp(key, "serverip") == 0) {
                strncpy(cfg->serverip, value, sizeof(cfg->serverip) - 1);
            } else if (strcmp(key, "port") == 0) {
                cfg->port = atoi(value);
            } else if (strcmp(key, "lcdtogglekey") == 0) {
                if (strcmp(value, "KEY_L") == 0) cfg->lcdtogglekey = KEY_L;
                else if (strcmp(value, "KEY_R") == 0) cfg->lcdtogglekey = KEY_R;
                else if (strcmp(value, "KEY_ZL") == 0) cfg->lcdtogglekey = KEY_ZL;
                else if (strcmp(value, "KEY_ZR") == 0) cfg->lcdtogglekey = KEY_ZR;
                else if (strcmp(value, "KEY_SELECT") == 0) cfg->lcdtogglekey = KEY_SELECT;
                else if (strcmp(value, "KEY_START") == 0) cfg->lcdtogglekey = KEY_START;
            }
        }
    }
    fclose(file);
}

int initsocket(config *cfg) {
    int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) return -1;
    
    struct sockaddr_in serveraddr;
    memset(&serveraddr, 0, sizeof(serveraddr));
    serveraddr.sin_family = AF_INET;
    serveraddr.sin_port = htons(cfg->port);
    
    if (inet_pton(AF_INET, cfg->serverip, &serveraddr.sin_addr) <= 0) {
        close(sockfd);
        return -1;
    }
    
    if (connect(sockfd, (struct sockaddr *)&serveraddr, sizeof(serveraddr)) < 0) {
        close(sockfd);
        return -1;
    }
    
    int flags = fcntl(sockfd, F_GETFL, 0);
    if (flags >= 0) {
        fcntl(sockfd, F_SETFL, flags | O_NONBLOCK);
    }
    
    return sockfd;
}

void togglelcd(bool *lcdon) {
    gspLcdInit();
    if (*lcdon) GSPLCD_PowerOffBacklight(GSPLCD_SCREEN_BOTH);
    else GSPLCD_PowerOnBacklight(GSPLCD_SCREEN_BOTH);
    gspLcdExit();
    *lcdon = !(*lcdon);
}

int main(int argc, char **argv) {
    gfxInitDefault();
    consoleInit(GFX_TOP, NULL);
    
    u32 *soc_buffer = (u32*)memalign(SOC_ALIGN, SOC_BUFFERSIZE);
    if (!soc_buffer) {
        gfxExit();
        return -1;
    }
    
    if (socInit(soc_buffer, SOC_BUFFERSIZE) != 0) {
        free(soc_buffer);
        gfxExit();
        return -1;
    }
    
    config cfg;
    readconfigfile(&cfg);
    
    int sockfd = initsocket(&cfg);
    int isconnected = 0;
    
    u64 currenttime = osGetTime();
    u64 last_ping_sent = currenttime;
    u64 last_pong_received = currenttime;
    u64 last_socket_reset = currenttime;
    bool lcdon = true;
    u32 frame_count = 0;
    
    if (sockfd >= 0) {
        send(sockfd, "ping", 5, 0);
    }
    
    while (aptMainLoop()) {
        hidScanInput();
        u32 kDown = hidKeysDown();
        u32 kHeld = hidKeysHeld();
        
        if ((kHeld & KEY_START) && (kHeld & KEY_SELECT)) break;
        
        if (kDown & cfg.lcdtogglekey) {
            togglelcd(&lcdon);
        }
        
        currenttime = osGetTime();
        
        if (sockfd < 0) {
            sockfd = initsocket(&cfg);
            last_socket_reset = currenttime;
            if (sockfd >= 0) send(sockfd, "ping", 5, 0);
        }
        
        if (sockfd >= 0) {
            char pong_buf[16];
            while (recv(sockfd, pong_buf, sizeof(pong_buf) - 1, 0) > 0) {
                last_pong_received = currenttime;
                isconnected = 1;
            }
            
            if (currenttime - last_ping_sent > 2000) {
                send(sockfd, "ping", 5, 0);
                last_ping_sent = currenttime;
            }
            
            // Auto-heal socket in <1ms if no pong for 4.5s (fixes 1-hour router key renewal!)
            if (currenttime - last_pong_received > 4500 && currenttime - last_socket_reset > 4500) {
                isconnected = 0;
                close(sockfd);
                sockfd = initsocket(&cfg);
                last_socket_reset = currenttime;
                if (sockfd >= 0) send(sockfd, "ping", 5, 0);
            }
        }
        
        controllerstate state;
        state.buttons = kHeld;
        hidCircleRead(&state.circlepad);
        hidCstickRead(&state.cstick);
        
        if (sockfd >= 0) {
            if (send(sockfd, &state, sizeof(controllerstate), 0) < 0) {
                if (errno != EAGAIN && errno != EWOULDBLOCK) {
                    close(sockfd);
                    sockfd = initsocket(&cfg);
                    last_socket_reset = currenttime;
                }
            }
        }
        
        if (frame_count % 10 == 0) {
            printf("\x1b[1;1H3DS Controller (60Hz Auto-Heal)");
            printf("\x1b[2;1HServer: %s:%d   ", cfg.serverip, cfg.port);
            printf("\x1b[3;1HStatus: %s   ", isconnected ? "\x1b[32mCONNECTED\x1b[0m" : "\x1b[31mRECONNECTING...\x1b[0m");
            printf("\x1b[5;1HButtons: 0x%08lX   ", state.buttons);
            printf("\x1b[6;1HCircle Pad: X=%4d Y=%4d   ", state.circlepad.dx, state.circlepad.dy);
            printf("\x1b[7;1HC-Stick:    X=%4d Y=%4d   ", state.cstick.dx, state.cstick.dy);
            printf("\x1b[9;1HPress START+SELECT to exit");
            gfxFlushBuffers();
            gfxSwapBuffers();
        }
        
        frame_count++;
        gspWaitForVBlank();
    }
    
    if (!lcdon) {
        gspLcdInit();
        GSPLCD_PowerOnBacklight(GSPLCD_SCREEN_BOTH);
        gspLcdExit();
    }
    
    if (sockfd >= 0) close(sockfd);
    socExit();
    free(soc_buffer);
    gfxExit();
    return 0;
}
