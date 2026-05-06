from __future__ import annotations
import collections
import math
import queue
import socket
import sys
import threading
import time
import os

import pygame

from pikart_shared import (
    HOST_PORT, VIEWPORT_WIDTH, VIEWPORT_HEIGHT, FPS,
    TOTAL_LAPS, MAX_PHYSICS_DT, COLOR_BACKGROUND, COLOR_PLAYER, COLOR_PLAYER2,
    COLOR_TRACK_P1, COLOR_TRACK_P2,
    STATE_MENU, STATE_WAITING, STATE_GAME, STATE_POST_RACE,
    Map, Camera, Shell, PowerupSpawner,
    draw_player_sprite, shell_from_dict, spawner_from_dict,
    DISCONNECTED, flush_queue, net_send_thread, net_recv_thread,
    make_button_rect, render_menu, render_status_screen, render_post_race,
    render_center_overlay_message, render_map, render_hud,
    build_minimap_surface, render_minimap,
    TRACK_HISTORY_MAX,
    joystick_init, joystick_stop, joystick_throttle, joystick_steer,
    joystick_consume_press, joystick_btn, joystick_menu_y, JS_SW,
    motor_init, motor_stop, motor_rumble, motor_update,
)

os.putenv('SDL_VIDEODRIVER', 'fbcon')
os.putenv('SDL_FBDEV', '/dev/fb0')
os.putenv('SDL_MOUSEDRV', 'dummy')
os.putenv('MOUSEDEV', '/dev/null')
os.putenv('DISPLAY', '')

DEBUG = 'debug' in sys.argv
HOST_IP = '127.0.0.1' if DEBUG else '192.168.50.1'

if not DEBUG:
    joystick_init()
    motor_init()


def log(msg: str):
    print(f"[CLIENT {time.strftime('%H:%M:%S')}] {msg}", flush=True)

# =============================================================================
# Network
# =============================================================================

CONNECT_RETRY_INTERVAL = 0.5


# tries to connect to the host until it succeeds or a DISCONNECTED sentinel arrives on result_q
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
            # check for cancellation between retries
            try:
                item = result_q.get_nowait()
                if item is DISCONNECTED:
                    log("connect_thread cancelled")
                    return
                result_q.put(item)
            except queue.Empty:
                pass

# =============================================================================
# Mirror vehicle (display-only, updated from network packets)
# =============================================================================

class MirrorVehicle:
    def __init__(self, color: tuple[int, int, int]):
        self.world_x: float = 0.0
        self.world_y: float = 0.0
        self.heading: float = 0.0
        self.max_lap: int = 1
        self.finish_place: int | None = None
        self.stored_powerup: str | None = None
        self.size_multiplier: float = 1.0
        self.color: tuple[int, int, int] = color
        self.track_history: collections.deque = collections.deque(maxlen=TRACK_HISTORY_MAX)

    # copies fields from a vehicle_to_dict packet onto this mirror
    def apply_state(self, data: dict):
        self.world_x = float(data['x'])
        self.world_y = float(data['y'])
        self.heading = float(data['heading'])
        self.max_lap = int(data['lap'])
        self.finish_place = data.get('place')
        self.stored_powerup = data.get('powerup')
        self.size_multiplier = float(data.get('size', 1.0))

    # draws the rotated player sprite at the screen position
    def draw(self, surface: pygame.Surface, screen_x: int, screen_y: int):
        draw_player_sprite(surface, screen_x, screen_y, self.color, self.size_multiplier,
                           self.heading)

# =============================================================================
# Main
# =============================================================================

pygame.init()
screen = pygame.display.set_mode((VIEWPORT_WIDTH, VIEWPORT_HEIGHT))
pygame.display.set_caption('PiKart - Client')
clock = pygame.time.Clock()
pygame.mouse.set_visible(False)

hud_font       = pygame.font.SysFont(None, 14)
place_font     = pygame.font.SysFont(None, 26)
countdown_font = pygame.font.SysFont(None, 52)
title_font     = pygame.font.SysFont(None, 44)
button_font    = pygame.font.SysFont(None, 22)

full_viewport = pygame.Rect(0, 0, VIEWPORT_WIDTH, VIEWPORT_HEIGHT)
play_button_rect = make_button_rect(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 - 27)
quit_button_rect = make_button_rect(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 + 27)
post_race_again_rect = make_button_rect(VIEWPORT_WIDTH // 2 - 60, VIEWPORT_HEIGHT // 2 + 10,
                                        width=100, height=30)
post_race_menu_rect = make_button_rect(VIEWPORT_WIDTH // 2, VIEWPORT_HEIGHT // 2 + 10,
                                       width=100, height=30)

current_state = STATE_MENU
error_msg: str | None = None
waiting_msg = 'Connecting...'
# 0=Play, 1=Quit
menu_sel = 0
# always 0; client shows only Exit to Menu
post_sel = 0

game_map: Map | None = None
map_surface: pygame.Surface | None = None
minimap_surface: pygame.Surface | None = None

p2_mirror = MirrorVehicle(color=COLOR_PLAYER2)
p1_mirror = MirrorVehicle(color=COLOR_PLAYER)
camera = Camera(VIEWPORT_WIDTH, VIEWPORT_HEIGHT)

p2_total_timer: float = 0.0
p2_lap_timer: float = 0.0
p2_prev_lap: int = 1
p2_race_finished = False

latest_countdown: float = 0.0
latest_go = False
latest_shells: list[Shell] = []
latest_spawners: list[PowerupSpawner] = []

connect_result_q = queue.Queue()
input_q = queue.Queue()
state_q = queue.Queue()
sock: socket.socket | None = None

KEY_FORWARD, KEY_BACK, KEY_LEFT, KEY_RIGHT = pygame.K_w, pygame.K_s, pygame.K_a, pygame.K_d


# closes the connection and signals the network threads; clears error_msg via the reason argument
def disconnect(reason: str | None):
    global sock, current_state, error_msg, waiting_msg
    log(f"disconnect: {reason!r}")
    if sock is not None:
        try: sock.close()
        except OSError: pass
        sock = None
    input_q.put(DISCONNECTED)
    flush_queue(state_q)
    error_msg = reason
    waiting_msg = 'Connecting...'
    current_state = STATE_MENU


# clears menu state and starts the connect thread; transitions to STATE_WAITING
def begin_connect():
    global current_state, error_msg, waiting_msg
    error_msg = None
    waiting_msg = 'Connecting...'
    flush_queue(connect_result_q)
    flush_queue(input_q)
    flush_queue(state_q)
    threading.Thread(target=connect_thread, args=(connect_result_q,), daemon=True).start()
    current_state = STATE_WAITING


# resets the per-race mirror state (timers, finish flag, latest entity lists, track histories)
def reset_race_state():
    global p2_total_timer, p2_lap_timer, p2_prev_lap, p2_race_finished
    global latest_shells, latest_spawners
    p2_total_timer = 0.0
    p2_lap_timer = 0.0
    p2_prev_lap = 1
    p2_race_finished = False
    latest_shells = []
    latest_spawners = []
    p1_mirror.track_history.clear()
    p2_mirror.track_history.clear()

# =============================================================================
# Main loop
# =============================================================================

running = True
while running:
    dt = min(clock.tick(FPS) / 1000.0, MAX_PHYSICS_DT)

    # connection result: promote the socket and start net threads, or surface a connect failure
    if current_state == STATE_WAITING and sock is None:
        try:
            result = connect_result_q.get_nowait()
            if result is DISCONNECTED or result is None:
                disconnect('Could not connect to host.')
            else:
                log("connected, starting net threads")
                sock = result
                sock.settimeout(None)
                flush_queue(input_q)
                flush_queue(state_q)
                threading.Thread(target=net_send_thread,
                                 args=(sock, input_q, state_q, log), daemon=True).start()
                threading.Thread(target=net_recv_thread,
                                 args=(sock, state_q, state_q, input_q, log), daemon=True).start()
        except queue.Empty:
            pass

    # drain incoming state packets and dispatch by message kind
    if current_state in (STATE_WAITING, STATE_GAME, STATE_POST_RACE):
        try:
            while True:
                pkt = state_q.get_nowait()
                if pkt is DISCONNECTED:
                    log(f"DISCONNECTED in state={current_state}")
                    disconnect('Connection lost.')
                    break
                if 'replay' in pkt:
                    waiting_msg = 'Player 1 is selecting a map...'
                    current_state = STATE_WAITING
                elif 'connected' in pkt:
                    waiting_msg = 'Player 1 is selecting a map...'
                elif 'map' in pkt and current_state == STATE_WAITING and sock is not None:
                    log(f"map: {pkt['map']!r}")
                    game_map = Map(pkt['map'] + '.txt')
                    map_surface = game_map.build_surface()
                    minimap_surface = build_minimap_surface(map_surface, game_map)
                    reset_race_state()
                    p2_mirror.apply_state({'x': game_map.player2_spawn[0],
                                           'y': game_map.player2_spawn[1],
                                           'heading': 0.0, 'lap': 1, 'place': None})
                    p1_mirror.apply_state({'x': game_map.player1_spawn[0],
                                           'y': game_map.player1_spawn[1],
                                           'heading': 0.0, 'lap': 1, 'place': None})
                    camera.center_on(p2_mirror.world_x, p2_mirror.world_y)
                    camera.heading = p2_mirror.heading
                elif 'state' in pkt:
                    latest_countdown = float(pkt.get('countdown', 0.0))
                    latest_go = bool(pkt.get('go', False))
                    p1_mirror.apply_state(pkt['p1'])
                    p2_mirror.apply_state(pkt['p2'])
                    # append a track-history sample on frames the host marked as a record frame
                    if current_state == STATE_GAME and pkt.get('track'):
                        pos1 = (p1_mirror.world_x, p1_mirror.world_y)
                        if not p1_mirror.track_history or p1_mirror.track_history[-1] != pos1:
                            p1_mirror.track_history.append(pos1)
                        pos2 = (p2_mirror.world_x, p2_mirror.world_y)
                        if not p2_mirror.track_history or p2_mirror.track_history[-1] != pos2:
                            p2_mirror.track_history.append(pos2)
                    latest_shells = [shell_from_dict(d) for d in pkt.get('shells', [])]
                    latest_spawners = [spawner_from_dict(d) for d in pkt.get('spawners', [])]
                    if not DEBUG:
                        if pkt.get('p2_collided', False):
                            motor_rumble()
                        if pkt.get('p2_shell_hit', False):
                            motor_rumble()
                    pkt_state = pkt.get('state')
                    if pkt_state == STATE_GAME and current_state == STATE_WAITING:
                        log("→ GAME")
                        current_state = STATE_GAME
                    elif pkt_state == STATE_POST_RACE and current_state != STATE_POST_RACE:
                        p2_race_finished = True
                        current_state = STATE_POST_RACE
        except queue.Empty:
            pass

    # p2 lap and total timers
    if current_state == STATE_GAME and latest_countdown <= 0.0 and not p2_race_finished:
        if p2_mirror.max_lap > p2_prev_lap:
            p2_lap_timer = 0.0
            p2_prev_lap = p2_mirror.max_lap
        if p2_mirror.finish_place is not None:
            p2_race_finished = True
        if not p2_race_finished:
            p2_total_timer += dt
            p2_lap_timer += dt

    # build and send the latest input packet (joystick or debug keyboard)
    if current_state == STATE_GAME and latest_countdown <= 0.0 and not p2_race_finished:
        if DEBUG:
            keys = pygame.key.get_pressed()
            throttle = float(int(keys[KEY_FORWARD]) - int(keys[KEY_BACK]))
            steer = float(int(keys[KEY_RIGHT]) - int(keys[KEY_LEFT]))
            activate = bool(keys[pygame.K_SPACE])
        else:
            throttle = joystick_throttle()
            steer = joystick_steer()
            activate = joystick_consume_press()
            if activate:
                motor_rumble()
        flush_queue(input_q)
        input_q.put({'throttle': throttle, 'steer': steer, 'activate': activate})

    # joystick menu navigation
    if not DEBUG and current_state in (STATE_MENU, STATE_POST_RACE):
        my = joystick_menu_y()
        if current_state == STATE_MENU and my != 0:
            menu_sel = 1 - menu_sel
        if joystick_consume_press():
            if current_state == STATE_MENU:
                if menu_sel == 0:
                    log("Play selected (joystick)")
                    begin_connect()
                else:
                    running = False
            elif current_state == STATE_POST_RACE:
                disconnect(None)

    # keyboard and mouse event handling
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            key = event.key
            if key == pygame.K_ESCAPE:
                if current_state in (STATE_GAME, STATE_POST_RACE, STATE_WAITING):
                    # cancel the connect thread if it's still spinning
                    if current_state == STATE_WAITING and sock is None:
                        connect_result_q.put(DISCONNECTED)
                    disconnect(None)
                else:
                    running = False
            elif current_state == STATE_MENU:
                if key in (pygame.K_UP, pygame.K_DOWN):
                    menu_sel = 1 - menu_sel
                elif key == pygame.K_SPACE:
                    if menu_sel == 0:
                        log("Play selected")
                        begin_connect()
                    else:
                        running = False
            elif current_state == STATE_POST_RACE:
                if key == pygame.K_SPACE:
                    disconnect(None)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            if current_state == STATE_MENU:
                if play_button_rect.collidepoint(pos):
                    log("Play clicked")
                    begin_connect()
                elif quit_button_rect.collidepoint(pos):
                    running = False
            elif current_state == STATE_POST_RACE:
                if post_race_menu_rect.collidepoint(pos):
                    disconnect(None)

    # menu / waiting render paths
    if current_state == STATE_MENU:
        render_menu(screen, play_button_rect, quit_button_rect,
                    title_font, button_font, error_msg, menu_sel)
        pygame.display.flip()
        continue

    if current_state == STATE_WAITING:
        render_status_screen(screen, title_font, button_font, waiting_msg)
        pygame.display.flip()
        continue

    # game / post-race: camera follows p2 with car-aligned heading
    camera.center_on(p2_mirror.world_x, p2_mirror.world_y)
    camera.heading = p2_mirror.heading

    if not DEBUG:
        motor_update()

    # render world, focal vehicle, hud, minimap, and any centered overlay messages
    screen.fill(COLOR_BACKGROUND)
    render_map(screen, map_surface, camera, full_viewport,
               overlay_vehicles=[p1_mirror], overlay_shells=latest_shells,
               overlay_spawners=latest_spawners,
               track_histories=[(p1_mirror.track_history, COLOR_TRACK_P1),
                                (p2_mirror.track_history, COLOR_TRACK_P2)])
    draw_player_sprite(screen, full_viewport.centerx, full_viewport.centery,
                       p2_mirror.color, p2_mirror.size_multiplier)
    render_hud(screen, full_viewport, p2_mirror.max_lap, TOTAL_LAPS,
               p2_total_timer, p2_lap_timer, p2_mirror.finish_place,
               hud_font, place_font, p2_mirror.stored_powerup)
    render_minimap(screen, minimap_surface, [p1_mirror, p2_mirror], game_map, full_viewport)

    if latest_countdown > 0.0:
        render_center_overlay_message(screen, str(int(math.ceil(latest_countdown))),
                                      countdown_font)
    elif latest_go:
        render_center_overlay_message(screen, 'GO!', countdown_font)

    if current_state == STATE_POST_RACE:
        render_post_race(screen, post_race_again_rect, post_race_menu_rect,
                         title_font, button_font, post_sel, show_play_again=False)

    pygame.display.flip()

pygame.quit()
if not DEBUG:
    joystick_stop()
    motor_stop()
sys.exit()