from typing import List, Dict
from datetime import datetime
from datetime import timedelta

from vnpy.trader.utility import ArrayManager, Interval
from vnpy.trader.object import TickData, BarData
from vnpy_portfoliostrategy import StrategyTemplate, StrategyEngine
from vnpy_portfoliostrategy.utility import PortfolioBarGenerator
from vnpy.trader.constant import Interval, Direction, Offset, Status

class XUReversionStrategy(StrategyTemplate):
    """"""

    author = "V"

    trading_start_time = "14:35:00"
    trading_end_time = "9:05:00"
    close_all_position_time = "9:30:00"
    thre = 0.01
    flat_thre = 0
    limit_pos = 6
    trade_size = 2
    algo_limit_place = 0.0
    algo_limit_spread = 1.0

    parameters = [
        "trading_start_time",
        "trading_end_time",
        "close_all_position_time",
        "thre",
        "flat_thre",
        "limit_pos",
        "trade_size",
        "algo_limit_place",
        "algo_limit_spread"
    ]

    current_pos = 0
    trading_in_process = False
    vt_orderids = -1
    next_level = -1
    last_price = -1
    anchor_price = -1
    long_value = -1
    short_value = -1

    variables = [
        "current_pos",
        "trading_in_process",
        "vt_orderids",
        "next_level",
        "last_price",
        "anchor_price",
        "long_value",
        "short_value"
    ]

    def __init__(self, strategy_engine: StrategyEngine, strategy_name: str, vt_symbols: List[str], setting: dict):
        """"""
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        self.last_tick_time: datetime = None
        self.targets: Dict[str, int] = {}

        self.t_trading_start_time = datetime.strptime(self.trading_start_time, '%H:%M:%S').time()
        self.t_trading_end_time = datetime.strptime(self.trading_end_time, '%H:%M:%S').time()
        self.t_close_all_position_time = datetime.strptime(self.close_all_position_time, '%H:%M:%S').time()
        self.bar_trading_start_time = datetime.strptime(self.trading_start_time, '%H:%M:%S') - timedelta(minutes=5)
        self.bar_trading_end_time = datetime.strptime(self.trading_end_time, '%H:%M:%S') - timedelta(minutes=5)
        self.bar_close_all_position_time = datetime.strptime(self.close_all_position_time, '%H:%M:%S') - timedelta(minutes=5)

        self.current_pos = 0
        self.next_level = -1
        self.trading_in_process = False
        self.vt_orderids = -1
        self.vt_orderids_datetime = -1
        self.last_price = -1
        self.anchor_price = -1
        self.long_value = -1
        self.short_value = -1

        self.open_orderids = []
        self.fake_orderids = []
        self.prev_traded = 0
        self.target = 0
        self.vt_symbol = vt_symbols[0]
        self.chase_interval = 20  # 拆单间隔:秒
        self.max_limit_reached = False
        self.prev_debug_message = ""
        self.debug_message = ""

        self.anchor_price_latest = -1
        self.anchor_debug = ""

        #For order handling
        self.chase_long_trigger = False
        self.chase_short_trigger = False
        self.last_vt_orderid = ""
        self.long_trade_volume = 0
        self.short_trade_volume = 0
        self.cancel_status = False

        # Obtain contract info
        self.ams: Dict[str, ArrayManager] = {}
        for vt_symbol in self.vt_symbols:
            self.ams[vt_symbol] = ArrayManager()
            self.targets[vt_symbol] = 0

        self.pbg = PortfolioBarGenerator(self.on_bars, 5, self.on_5min_bar, Interval.MINUTE)    # 5 = 5min
        self.put_event()

    def on_init(self):
        """
        Callback when strategy is inited.
        """
        self.write_log("策略初始化")

        self.load_bars(6)   # 6 = 6 days
        # self.write_log(self.anchor_debug)

    def on_start(self):
        """
        Callback when strategy is started.
        """
        self.write_log("策略启动")
        self.t_trading_start_time = datetime.strptime(self.trading_start_time, '%H:%M:%S').time()
        self.t_trading_end_time = datetime.strptime(self.trading_end_time, '%H:%M:%S').time()
        self.t_close_all_position_time = datetime.strptime(self.close_all_position_time, '%H:%M:%S').time()
        self.bar_trading_start_time = datetime.strptime(self.trading_start_time, '%H:%M:%S') - timedelta(minutes=5)
        self.bar_trading_end_time = datetime.strptime(self.trading_end_time, '%H:%M:%S') - timedelta(minutes=5)
        self.bar_close_all_position_time = datetime.strptime(self.close_all_position_time, '%H:%M:%S') - timedelta(minutes=5)

        # self.current_pos = 0
        # self.next_level = -1
        # self.trading_in_process = False
        # self.vt_orderids = -1
        # self.vt_orderids_datetime = -1
        # self.last_price = -1
        # self.anchor_price = -1
        # self.long_value = -1
        # self.short_value = -1

        # self.open_orderids = []
        # self.fake_orderids = []
        # self.prev_traded = 0
        # self.target = 0
        # self.vt_symbol = vt_symbols[0]
        # self.chase_interval = 20  # 拆单间隔:秒
        # self.max_limit_reached = False
        # self.prev_debug_message = ""
        # self.debug_message = ""

        #For order handling
        # self.chase_long_trigger = False
        # self.chase_short_trigger = False
        # self.last_vt_orderid = ""
        # self.long_trade_volume = 0
        # self.short_trade_volume = 0
        # self.cancel_status = False

        # Put Anchor into self.anchor_price
        self.anchor_price = self.anchor_price_latest

        # Get saved pos for further action (e.g. close all after trading period)
        self.current_pos = self.get_pos(self.vt_symbol)
        self.write_log(f"outstanding pos : {self.current_pos}")
        current_time = datetime.now().time()

        # Close all position if 09:30 - 14:35
        if self.current_pos != 0:
            close_position_period = self.time_in_close_position_period(self.t_close_all_position_time, self.t_trading_start_time, current_time)
            if close_position_period:
                if self.current_pos > 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all outstanding pos outside trading period. Begin SHORT {abs(self.current_pos)} pos.")
                elif self.current_pos < 0:
                    self.target = -self.current_pos
                    self.write_log(f"Close all outstanding pos outside trading period. Begin LONG {abs(self.current_pos)} pos.")

        # Check anchor level if 14:35 to 9:00
        in_trading_period = self.time_in_trading_period(self.t_trading_start_time, self.t_trading_end_time, current_time)
        if in_trading_period:
            tick = self.get_tick(self.vt_symbol)
            # self.write_log(tick)
            calculated_pos = self.get_calculated_pos(tick)
            self.write_log(f"calculated pos : {calculated_pos}")
            pos_offset = calculated_pos - self.current_pos
            self.write_log(f"Doing position offset(trades {pos_offset}). Total Pos : {self.current_pos}")
            if (pos_offset != 0):
                self.target = pos_offset

        self.put_event()

    def on_stop(self):
        """
        Callback when strategy is stopped.
        """
        self.cancel_all()
        self.write_log("策略停止")

    def on_tick(self, tick: TickData):
        """
        Callback of new tick data update.
        """
        self.pbg.update_tick(tick)
        if self.trading == False:
            return
        self.last_price = tick.last_price
        self.current_pos = self.get_pos(self.vt_symbol)

        ########## Check last price with anchor to determine new target ##########
        current_time = datetime.now().time()
        in_trading_period = self.time_in_trading_period(self.t_trading_start_time, self.t_trading_end_time, current_time)
        if self.trading_in_process == False and self.target == 0 and in_trading_period and self.anchor_price != -1 and tick.bid_price_1 != -1:
            long = False
            short = False
            self.next_level = abs(self.current_pos / self.trade_size) + 1
            self.short_value = self.anchor_price * (1 + self.thre * self.next_level)
            self.long_value = self.anchor_price * (1 - self.thre * self.next_level)
            # self.write_log(f"short value : {self.short_value}, long value : {self.long_value}, next level is {self.next_level}")
            if (tick.last_price > (self.short_value)):
                short = True
                if self.max_limit_reached == False:
                    self.write_log(f"last_price > {self.short_value}. Begin short {self.trade_size} pos.")
            if (tick.last_price < (self.long_value)):
                long = True
                if self.max_limit_reached == False:
                    self.write_log(f"last_price < {self.long_value}. Begin long {self.trade_size} pos.")
            if self.current_pos > 0 and tick.last_price > self.anchor_price:
                self.max_limit_reached = False
                self.write_log(f"Last price > Anchor Price. Begin close all LONG position.")
                self.target = -self.current_pos
            if self.current_pos < 0 and tick.last_price < self.anchor_price:
                self.max_limit_reached = False
                self.write_log(f"Last price < Anchor Price. Begin close all SHORT position.")
                self.target = -self.current_pos

            if (abs(self.current_pos) >= self.limit_pos and (long or short)):
                if self.max_limit_reached == False:
                    self.write_log(f"Max limit({self.limit_pos}) position reached. Skipped for new order. LONG<{long}> SHORT<{short}>")
                    self.max_limit_reached = True
            else:
                self.max_limit_reached = False
                if (long):
                    self.target = self.trade_size
                if (short):
                    self.target = -self.trade_size


        ########## Place order when new target ##########
        # self.write_log(tick)
        if self.target != 0 and tick.bid_price_1 != -1:
            if self.target > 0:
                price = tick.bid_price_1 + self.algo_limit_place
                volume = abs(self.target)
                self.vt_orderids = self.buy(self.vt_symbol, price, volume)
                self.vt_orderids_datetime = datetime.now()
                self.open_orderids.extend(self.vt_orderids)
                self.write_log(f"LONG {volume} limit order (id:{self.vt_orderids}) sent with bid({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
            elif self.target < 0:
                price = tick.ask_price_1 - self.algo_limit_place
                volume = abs(self.target)
                self.vt_orderids = self.short(self.vt_symbol, price, volume)
                self.vt_orderids_datetime = datetime.now()
                self.open_orderids.extend(self.vt_orderids)
                self.write_log(f"SHORT {volume} limit order (id:{self.vt_orderids}) sent with ask({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
            self.target = 0
            self.trading_in_process = True

        ########## Print when order traded ##########
        # open_orderids
        pending_order = False
        order_summary = ""
        for vt_orderid in self.open_orderids:
            order = self.get_order(vt_orderid)
            if order:
                if order.traded > 0:
                    if order.traded != self.prev_traded:
                        # self.write_log(f"{order}")
                        traded_offset = order.traded - self.prev_traded
                        self.write_log(f"Order (id:{vt_orderid}) with pos {traded_offset} traded. {order.direction} {order.traded}/{order.volume} {order.symbol} @ {order.price} ")
                        # if order.direction == Direction.LONG:
                        #     self.current_pos = self.current_pos + traded_offset
                        # elif order.direction == Direction.SHORT:
                        #     self.current_pos = self.current_pos - traded_offset
                        self.prev_traded = order.traded

                if order.status == Status.ALLTRADED:
                    self.write_log(f"Order (id:{vt_orderid}) all traded. {order.direction} {order.traded}/{order.volume} {order.symbol} @ {order.price} ")
                    self.trading_in_process = False
                    self.prev_traded = 0
                    self.open_orderids.remove(vt_orderid)
                if order.status == Status.PARTTRADED:
                    self.write_log(f"Order {vt_orderid} partially placed. {order.direction} {order.traded}/{order.volume} {order.symbol} @ {order.price}. ")
                if order.status == Status.CANCELLED or order.status == Status.REJECTED:
                    self.open_orderids.remove(vt_orderid)
                if order.status == Status.SUBMITTING or order.status == Status.PARTTRADED or order.status == Status.NOTTRADED:
                    pending_order = True

                order_summary = order_summary + f"{vt_orderid}<{order.status.value}>,"
        # if pending_order == False and self.trading_in_process:
        #     self.write_log(f"No pending order and continue the latest tick data checking.")
        #     self.trading_in_process = False


        ########## Cancel old order when exceed algo limit and place new order ##########
        # get_all_active_orderids
        all_active_orderids = self.get_all_active_orderids()
        fake_submitting = False
        no_active_orderids = True
        if all_active_orderids:
            order_finished = False
            for vt_orderid in all_active_orderids:  # vt_orderid = all_active_orderids[0] # for vt_orderid in all_active_orderids:
                if vt_orderid in self.fake_orderids:
                    continue
                no_active_orderids = False
                self.last_vt_orderid = vt_orderid
                order = self.get_order(vt_orderid)
                # self.write_log(f"{order}")
                redo_long = False
                redo_short = False

                if order:   #if order.status == Status.PARTTRADED or order.status == Status.NOTTRADED or order.status == Status.SUBMITTING:
                    if order.direction == Direction.LONG and (tick.bid_price_1 - order.price) > self.algo_limit_spread and self.chase_long_trigger == False and (order.volume - order.traded) > 0:
                        #cancel the original order and make a new one
                        redo_long = True
                        self.write_log(f"Cancel LONG order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}] - Algo limit spread threshold exceeded. Cancel and place a new order - bid:{tick.bid_price_1} price:{order.price} spread:{self.algo_limit_spread}")
                    if order.direction == Direction.SHORT and (order.price - tick.ask_price_1) > self.algo_limit_spread and self.chase_short_trigger == False and (order.volume - order.traded) > 0:
                        #cancel the original order and make a new one
                        redo_short = True
                        self.write_log(f"Cancel SHORT order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}] - Algo limit spread threshold exceeded. Cancel and place a new order - ask:{tick.ask_price_1} price:{order.price} spread:{self.algo_limit_spread}")

                    # self.write_log(f"{order}")
                    # self.write_log(f"{tick.datetime.replace(tzinfo=None)} - {self.vt_orderids_datetime}")
                    if order.direction == Direction.LONG and order.status == Status.SUBMITTING and order.traded == 0 and \
                            (tick.datetime.replace(tzinfo=None) - self.vt_orderids_datetime).total_seconds() > self.chase_interval:
                        #fake submitting on hold
                        fake_submitting = True
                        redo_long = True
                        self.fake_orderids.append(vt_orderid)
                        self.write_log(f"Fake SUBMITTING Order. Cancel LONG order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}]. Cancel and place a new order - bid:{tick.bid_price_1} price:{order.price} spread:{self.algo_limit_spread}")
                    if order.direction == Direction.SHORT and order.status == Status.SUBMITTING and order.traded == 0 and \
                            (tick.datetime.replace(tzinfo=None) - self.vt_orderids_datetime).total_seconds() > self.chase_interval:
                        #fake submitting on hold
                        fake_submitting = True
                        redo_short = True
                        self.fake_orderids.append(vt_orderid)
                        self.write_log(f"Fake SUBMITTING Order. Cancel SHORT order (id:{vt_orderid}) [Traded:{order.traded}/{order.volume}]. Cancel and place a new order - bid:{tick.ask_price_1} price:{order.price} spread:{self.algo_limit_spread}")

                    if redo_long:
                        self.write_log(f"{order}")
                        price = tick.bid_price_1 + self.algo_limit_place
                        untrade_volume = order.volume - order.traded
                        self.long_trade_volume = untrade_volume
                        self.cancel_order(vt_orderid)
                        self.chase_long_trigger = True
                    if redo_short:
                        self.write_log(f"{order}")
                        price = tick.ask_price_1 - self.algo_limit_place
                        untrade_volume = order.volume - order.traded
                        self.short_trade_volume = untrade_volume
                        self.cancel_order(vt_orderid)
                        self.chase_short_trigger = True
        if no_active_orderids:
            order_finished = True
            self.cancel_status = False

        order = self.get_order(self.last_vt_orderid)
        if order is not None:
            if (order.status == Status.CANCELLED or order.status == Status.REJECTED) or fake_submitting:

                if self.chase_long_trigger == False and self.chase_short_trigger == False:
                    # Got automatic cancel
                    order = self.get_order(self.last_vt_orderid)
                    if order.direction == Direction.LONG:
                        #cancel the original order and make a new one
                        self.write_log(f"{order}")
                        price = tick.bid_price_1 + self.algo_limit_place
                        untrade_volume = order.volume - order.traded
                        self.long_trade_volume = untrade_volume
                        self.chase_long_trigger = True
                        self.write_log(f"Got cancel LONG order (id:{self.last_vt_orderid}) [Traded:{order.traded}/{order.volume}]. Place a new order - bid:{tick.bid_price_1} price:{order.price} spread:{self.algo_limit_spread}")

                    if order.direction == Direction.SHORT:
                        #cancel the original order and make a new one
                        self.write_log(f"{order}")
                        price = tick.ask_price_1 - self.algo_limit_place
                        untrade_volume = order.volume - order.traded
                        self.short_trade_volume = untrade_volume
                        self.chase_short_trigger = True
                        self.write_log(f"Got cancel SHORT order (id:{self.last_vt_orderid}) [Traded:{order.traded}/{order.volume}]. Place a new order - ask:{tick.ask_price_1} price:{order.price} spread:{self.algo_limit_spread}")


                if self.chase_long_trigger:
                    if order_finished or fake_submitting:
                        price = tick.bid_price_1 + self.algo_limit_place
                        self.vt_orderids = self.buy(self.vt_symbol, price, self.long_trade_volume)
                        self.vt_orderids_datetime = datetime.now()
                        self.open_orderids.extend(self.vt_orderids)
                        self.write_log(f"New LONG {self.long_trade_volume} limit order (id:{self.vt_orderids}) sent with bid({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
                        self.long_trade_volume = 0
                        self.chase_long_trigger = False
                        self.prev_traded = 0
                    else:
                        self.cancel_surplus_order(all_active_orderids)
                elif self.chase_short_trigger:
                    if order_finished or fake_submitting:
                        price = tick.ask_price_1 - self.algo_limit_place
                        self.vt_orderids = self.short(self.vt_symbol, price, self.short_trade_volume)
                        self.vt_orderids_datetime = datetime.now()
                        self.open_orderids.extend(self.vt_orderids)
                        self.write_log(f"New SHORT {self.short_trade_volume} limit order (id:{self.vt_orderids}) sent with ask({price}). (bid ask:{tick.bid_price_1} {tick.ask_price_1})")
                        self.short_trade_volume = 0
                        self.chase_short_trigger = False
                        self.prev_traded = 0
                    else:
                        self.cancel_surplus_order(all_active_orderids)



        if self.trading:
            self.debug_message = (f"Debug:Trading<{self.trading_in_process}>,Anchor<{self.anchor_price}>,Target<{self.target}>,CurrPos<{self.current_pos}>,order_finished<{order_finished}>, " +
                       f"cancel_status<{self.cancel_status}>,chase_long_trigger<{self.chase_long_trigger}>,chase_short_trigger<{self.chase_short_trigger}>, " +
                       f"OpenOrder<{self.open_orderids}>,<{self.get_all_active_orderids()}>,order_summary[{order_summary}], fake_orderids[{self.fake_orderids}]")
            if self.prev_debug_message != self.debug_message:
                self.write_log(self.debug_message)
                self.prev_debug_message = self.debug_message
        self.put_event()

    def on_bars(self, bars: Dict[str, BarData]):
        """
        Callback of new bars data update.
        """
        self.pbg.update_bars(bars)

    def on_5min_bar(self, bars: Dict[str, BarData]):
        """"""
        self.cancel_all()
        # self.write_log(bars)

        for vt_symbol, bar in bars.items():

            # Get Anchor at 14:30
            if bar.datetime.hour == self.bar_trading_start_time.hour and bar.datetime.minute == self.bar_trading_start_time.minute:   #on bar close, so the bar of 14:30 means 14:35
                # self.write_log(bars)
                am: ArrayManager = self.ams[vt_symbol]
                am.update_bar(bar)
                # tick = self.pbg.last_ticks[vt_symbol]
                self.write_log(f"{bar.datetime} : {bar.close_price}, count : {am.count}")
                self.anchor_price_latest = bar.close_price
                self.anchor_price = bar.close_price
                # self.anchor_debug = self.anchor_debug + f"{bar.close_price}, "

            # Stop trading after 9:00
            if bar.datetime.hour == self.bar_trading_end_time.hour and bar.datetime.minute == self.bar_trading_end_time.minute:
                self.anchor_price_latest = -1
                self.anchor_price = -1
                if self.trading:
                    self.write_log(f"Stop trading after 9:00.")

            # Close all pos when 9:30
            if bar.datetime.hour == self.bar_close_all_position_time.hour and bar.datetime.minute == self.bar_close_all_position_time.minute:
                self.anchor_price_latest = -1
                self.anchor_price = -1
                if self.trading:
                    self.write_log(f"Close all pos at {self.close_all_position_time} if any.")
                    if self.current_pos > 0:
                        self.target = -self.current_pos
                        self.write_log(f"Close all pos. Begin SHORT {abs(self.current_pos)} pos.")
                    elif self.current_pos < 0:
                        self.target = -self.current_pos
                        self.write_log(f"Close all pos. Begin LONG {abs(self.current_pos)} pos.")

        self.put_event()

    def time_in_trading_period(self, start, end, current):    #working only with 14:35 to 09:00
        """Returns whether current is in the range [start, end]"""
        return start <= current or current <= end

    def time_in_close_position_period(self, start, end, current):
        """Returns whether current is in the range [start, end]"""
        return start <= current and current <= end

    def cancel_surplus_order(self,orderids:list):
        """
        撤销剩余活动委托单
        """
        if not self.cancel_status:
            for vt_orderid in orderids:
                self.cancel_order(vt_orderid)
                self.write_log(f"cancel_surplus_order:{vt_orderid}")
            self.cancel_status = True

    def get_calculated_pos(self, tick):
        calc_pos = 0
        last_price = 0
        if tick.last_price > 0:
            last_price = tick.last_price
        else:
            if tick.bid_price_1 > 0 and tick.ask_price_1 > 0:
                last_price = (tick.bid_price_1 + tick.ask_price_1)/2
            else:
                return 888

        if self.anchor_price != -1:
            while abs(calc_pos) < self.limit_pos:
                self.short_value = self.anchor_price * (1 + self.thre * (abs(calc_pos/self.trade_size) + 1))
                self.long_value = self.anchor_price * (1 - self.thre * (abs(calc_pos/self.trade_size) + 1))
                self.next_level = abs(calc_pos/self.trade_size) + 1
                self.write_log(f"calculating pos : short<{self.short_value}> long<{self.long_value}>, next level is {self.next_level}")
                if last_price > self.short_value:
                    calc_pos = calc_pos - self.trade_size
                elif last_price < self.long_value:
                    calc_pos = calc_pos + self.trade_size
                else:
                    break
            return calc_pos
        else:
            return 999