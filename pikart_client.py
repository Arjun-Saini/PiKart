from __future__ import annotations
import os
import math
import queue
import socket
import sys
import threading
import time

import pygame

from pikart_shared import (
    HOST_IP, HOST_PORT, VIEWPORT_WIDTH, VIEWPORT_HEIGHT, FPS,
    TOTAL_LAPS, MAX_PHYSICS_DT, COLOR_BACKGROUND, COLOR_PLAYER, COLOR_PLAYER2,
    STATE_MENU, STATE_WAITING, STATE_GAME, STATE_POST_RACE,
    Map, Camera, Shell, PowerupSpawner, PLAYER_SIZE,
    DISCONNECTED, flush_queue, net_send_thread, net_recv_thread,
    make_button_rect, render_menu, render_status_screen, render_post_race,
    render_center_overlay_message, render_map, render_hud,
    send_msg, recv_msg,
    joystick_init, joystick_stop, joystick_throttle, joystick_steer,
    joystick_btn_pressed, joystick_menu_y, JS_SW2,
)

DEBUG = 'debug' in sys.argv
if not DEBUG:
    joystick_init()

def log(msg: str):
    print(f"[CLIENT {time.strftime('%H:%M:%S')}] {msg}", flush=True)

# =============================================================================
# Network
# =============================================================================

CONNECT_RETRY_INTERVAL = 0.5


def connect_thread(result_q: queue.Queue):
    log("connect_thread started")
    attempt = 0
    while True:
        attempt += 1
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            log(f"attempt {attempt} → {HOST_IP}:{HOST_PORT}")
            sock.connect((HOST_IP, HOST_PORT))
            log(f"attempt {attempt} succeeded")
            result_q.put(sock)
            return
        except OSError as e:
            log(f"attempt {attempt} failed: {e}")
            sock.close()
            time.sleep(CONNECT_RETRY_INTERVAL)
            try:
                item = result_q.get_nowait()
                if item is DISCONNECTED:
                    log("connect_thread cancelled")
                    return
                result_q.put(item)
            except queue.Empty:
                pass


def start_net_threads(sock: socket.socket, input_q: queue.Queue, state_q: queue.Queue):
    log("starting net threads")
    threading.Thread(target=net_send_thread, args=(sock, input_q, state_q, log), daemon=True).start()
    threading.Thread(target=net_recv_thread, args=(sock, state_q, state_q, input_q, log), daemon=True).start()

# =============================================================================
# Mirror vehicle (display-only, updated from network packets)
# =============================================================================

class MirrorVehicle:
    def __init__(self, color: tuple[int, int, int]):
        self.world_x:         float = 0.0
        self.world_y:         float = 0.0
        self.heading:         float = 0.0
        self.max_lap:         int   = 1
        self.finish_place:    int | None = None
        self.stored_powerup:  str | None = None
        self.size_multiplier: float = 1.0
        self.color = color

    def apply_state(self, data: dict):
        self.world_x         = float(data['x'])
        self.world_y         = float(data['y'])
        self.heading         = float(data['heading'])
        self.max_lap         = int(data['lap'])
        self.finish_place    = data.get('place')
        self.stored_powerup  = data.get('powerup')
        self.size_multiplier = float(data.get('size', 1.0))

    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        size = int(PLAYER_SIZE * self.size_multiplier)
        h    = size // 2
        rect = pygame.Rect(screen_x - h, screen_y - h, size, size)
        pygame.draw.rect(surface, self.color, rect)
        pygame.draw.rect(surface, (0, 0, 0), rect, 1)

# =============================================================================
# Main
# =============================================================================

pygame.init()
screen = pygame.display.set_mode((VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
pygame.display.set_caption('PiKart — Client')
clock = pygame.time.Clock()

hud_font       = pygame.font.SysFont(None, 14)
place_font     = pygame.font.SysFont(None, 26)
countdown_font = pygame.font.SysFont(None, 52)
title_font     = pygame.font.SysFont(None, 44)
button_font    = pygame.font.SysFont(None, 22)

full_viewport        = pygame.Rect(0, 0, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
play_button_rect     = make_button_rect(VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//2 - 27)
quit_button_rect     = make_button_rect(VIEWPORT_WIDTH//2, VIEWPORT_HEIGHT//2 + 27)
post_race_again_rect = make_button_rect(VIEWPORT_WIDTH//2 - 60, VIEWPORT_HEIGHT//2 + 10, width=100, height=30)
post_race_menu_rect  = make_button_rect(VIEWPORT_WIDTH//2 + 60, VIEWPORT_HEIGHT//2 + 10, width=100, height=30)

current_state = STATE_MENU
error_msg: str | None = None
waiting_msg  = 'Connecting...'
menu_sel     = 0   # 0=Play, 1=Quit
post_sel     = 1   # 0=Play Again (inactive on client), 1=Exit to Menu

game_map: Map | None               = None
map_surface: pygame.Surface | None = None

p2_mirror = MirrorVehicle(color=COLOR_PLAYER2)
p1_mirror = MirrorVehicle(color=COLOR_PLAYER)
camera    = Camera(VIEWPORT_WIDTH, VIEWPORT_HEIGHT)

p2_total_timer:   float = 0.0
p2_lap_timer:     float = 0.0
p2_prev_lap:      int   = 1
p2_race_finished        = False

latest_countdown: float = 0.0
latest_go               = False
latest_shells:   list[Shell]          = []
latest_spawners: list[PowerupSpawner] = []

connect_result_q = queue.Queue()
input_q          = queue.Queue()
state_q          = queue.Queue()
sock: socket.socket | None = None

KEY_FORWARD, KEY_BACK, KEY_LEFT, KEY_RIGHT = pygame.K_w, pygame.K_s, pygame.K_a, pygame.K_d


def disconnect(reason: str | None):
    global sock, current_state, error_msg, waiting_msg
    log(f"disconnect: {reason!r}")
    if sock is not None:
        try: sock.close()
        except OSError: pass
        sock = None
    input_q.put(DISCONNECTED)
    flush_queue(state_q)
    error_msg = reason; waiting_msg = 'Connecting...'; current_state = STATE_MENU


def reset_race_state():
    global p2_total_timer, p2_lap_timer, p2_prev_lap, p2_race_finished, latest_shells, latest_spawners
    p2_total_timer = p2_lap_timer = 0.0
    p2_prev_lap = 1; p2_race_finished = False; latest_shells = []; latest_spawners = []

# =============================================================================
# Main loop
# =============================================================================

running = True
while running:
    dt = min(clock.tick(FPS) / 1000.0, MAX_PHYSICS_DT)

    if current_state == STATE_WAITING and sock is None:
        try:
            result = connect_result_q.get_nowait()
            if result is DISCONNECTED or result is None:
                disconnect('Could not connect to host.')
            else:
                log("connected")
                sock = result
                sock.settimeout(None)
                flush_queue(input_q)
                flush_queue(state_q)
                start_net_threads(sock, input_q, state_q)
        except queue.Empty:
            pass

    if current_state in (STATE_WAITING, STATE_GAME, STATE_POST_RACE):
        try:
            while True:
                pkt = state_q.get_nowait()
                if pkt is DISCONNECTED:
                    log(f"DISCONNECTED in state={current_state}")
                    disconnect('Connection lost.')
                    break
                if 'replay' in pkt:
                    waiting_msg = 'Player 1 is selecting a map...'; current_state = STATE_WAITING
                elif 'connected' in pkt:
                    waiting_msg = 'Player 1 is selecting a map...'
                elif 'map' in pkt and current_state == STATE_WAITING and sock is not None:
                    log(f"map: {pkt['map']!r}")
                    game_map    = Map(pkt['map'] + '.txt')
                    map_surface = game_map.build_surface()
                    reset_race_state()
                    p2_mirror.apply_state({'x': game_map.player2_spawn[0], 'y': game_map.player2_spawn[1],
                                           'heading': 0.0, 'lap': 1, 'place': None})
                    p1_mirror.apply_state({'x': game_map.player1_spawn[0], 'y': game_map.player1_spawn[1],
                                           'heading': 0.0, 'lap': 1, 'place': None})
                    camera.center_on(p2_mirror.world_x, p2_mirror.world_y)
                    camera.heading = p2_mirror.heading
                elif 'state' in pkt:
                    latest_countdown = float(pkt.get('countdown', 0.0))
                    latest_go        = bool(pkt.get('go', False))
                    p1_mirror.apply_state(pkt['p1'])
                    p2_mirror.apply_state(pkt['p2'])
                    latest_shells   = [Shell.from_dict(d) for d in pkt.get('shells', [])]
                    latest_spawners = [PowerupSpawner.from_dict(d) for d in pkt.get('spawners', [])]
                    pkt_state = pkt.get('state')
                    if pkt_state == STATE_GAME and current_state == STATE_WAITING:
                        log("→ GAME"); current_state = STATE_GAME
                    elif pkt_state == STATE_POST_RACE and current_state != STATE_POST_RACE:
                        p2_race_finished = True; current_state = STATE_POST_RACE
        except queue.Empty:
            pass

    if current_state == STATE_GAME and latest_countdown <= 0.0 and not p2_race_finished:
        if p2_mirror.max_lap > p2_prev_lap:
            p2_lap_timer = 0.0; p2_prev_lap = p2_mirror.max_lap
        if p2_mirror.finish_place is not None:
            p2_race_finished = True
        if not p2_race_finished:
            p2_total_timer += dt; p2_lap_timer += dt

    if current_state == STATE_GAME and latest_countdown <= 0.0 and not p2_race_finished:
        if DEBUG:
            keys = pygame.key.get_pressed()
            throttle = float(int(keys[KEY_FORWARD]) - int(keys[KEY_BACK]))
            steer    = float(int(keys[KEY_RIGHT])   - int(keys[KEY_LEFT]))
            activate = bool(keys[pygame.K_SPACE])
        else:
            throttle = joystick_throttle()
            steer    = joystick_steer()
            activate = joystick_btn_pressed(JS_SW2)
        flush_queue(input_q)
        input_q.put({'throttle': throttle, 'steer': steer, 'activate': activate})

    # joystick menu navigation
    if not DEBUG and current_state in (STATE_MENU, STATE_POST_RACE):
        my = joystick_menu_y()
        if current_state == STATE_MENU and my != 0:
            menu_sel = 1 - menu_sel
        if joystick_btn_pressed(JS_SW2):
            if current_state == STATE_MENU:
                if menu_sel == 0:
                    log("Play selected (joystick)")
                    error_msg = None; waiting_msg = 'Connecting...'
                    flush_queue(connect_result_q); flush_queue(input_q); flush_queue(state_q)
                    threading.Thread(target=connect_thread, args=(connect_result_q,), daemon=True).start()
                    current_state = STATE_WAITING
                else:
                    running = False
            elif current_state == STATE_POST_RACE and post_sel == 1:
                disconnect(None); error_msg = None

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            key = event.key
            if key == pygame.K_ESCAPE:
                if current_state in (STATE_GAME, STATE_POST_RACE, STATE_WAITING):
                    if current_state == STATE_WAITING and sock is None:
                        connect_result_q.put(DISCONNECTED)
                    disconnect(None); error_msg = None
                else:
                    running = False

            elif current_state == STATE_MENU:
                if key in (pygame.K_UP, pygame.K_DOWN):
                    menu_sel = 1 - menu_sel
                elif key == pygame.K_SPACE:
                    if menu_sel == 0:
                        log("Play selected")
                        error_msg = None; waiting_msg = 'Connecting...'
                        flush_queue(connect_result_q); flush_queue(input_q); flush_queue(state_q)
                        threading.Thread(target=connect_thread, args=(connect_result_q,), daemon=True).start()
                        current_state = STATE_WAITING
                    else:
                        running = False

            elif current_state == STATE_POST_RACE:
                if key == pygame.K_SPACE and post_sel == 1:
                    disconnect(None); error_msg = None

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            if current_state == STATE_MENU:
                if play_button_rect.collidepoint(pos):
                    log("Play clicked")
                    error_msg = None; waiting_msg = 'Connecting...'
                    flush_queue(connect_result_q); flush_queue(input_q); flush_queue(state_q)
                    threading.Thread(target=connect_thread, args=(connect_result_q,), daemon=True).start()
                    current_state = STATE_WAITING
                elif quit_button_rect.collidepoint(pos):
                    running = False
            elif current_state == STATE_POST_RACE:
                if post_race_menu_rect.collidepoint(pos):
                    disconnect(None); error_msg = None

    if current_state == STATE_MENU:
        render_menu(screen, play_button_rect, quit_button_rect, title_font, button_font, error_msg, menu_sel)
        pygame.display.flip(); continue

    if current_state == STATE_WAITING:
        render_status_screen(screen, title_font, button_font, waiting_msg)
        pygame.display.flip(); continue

    camera.center_on(p2_mirror.world_x, p2_mirror.world_y)
    camera.heading = p2_mirror.heading

    screen.fill(COLOR_BACKGROUND)
    render_map(screen, map_surface, camera, full_viewport,
               overlay_vehicles=[p1_mirror], overlay_shells=latest_shells, overlay_spawners=latest_spawners)
    p2_mirror.draw(screen, full_viewport.centerx, full_viewport.centery)
    render_hud(screen, full_viewport, p2_mirror.max_lap, TOTAL_LAPS,
               p2_total_timer, p2_lap_timer, p2_mirror.finish_place,
               hud_font, place_font, p2_mirror.stored_powerup)

    if latest_countdown > 0.0:
        render_center_overlay_message(screen, str(int(math.ceil(latest_countdown))), countdown_font)
    elif latest_go:
        render_center_overlay_message(screen, 'GO!', countdown_font)

    if current_state == STATE_POST_RACE:
        render_post_race(screen, post_race_again_rect, post_race_menu_rect, title_font, button_font, post_sel)

    pygame.display.flip()

pygame.quit()
if not DEBUG:
    joystick_stop()
sys.exit()