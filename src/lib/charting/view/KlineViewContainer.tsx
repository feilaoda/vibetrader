import React, { Component, Fragment, type JSX } from "react";
import html2canvas from "html2canvas";
import { KlineView } from "./KlineView";
import { VolumeView } from "./VolumeView";
import { ChartXControl } from "./ChartXControl";
import { ChartView, type CallbacksToContainer, type Indicator, type UpdateDrawing, type UpdateEvent } from "./ChartView";
import AxisX from "../pane/AxisX";
import type { TSer } from "../../timeseris/TSer";
import type { TVar } from "../../timeseris/TVar";
import { Kline, KVAR_NAME } from "../../domain/Kline";
import { Path } from "../../svg/Path";
import Title from "../pane/Title";
import { Help } from "../pane/Help";
import { IndicatorView } from "./IndicatorView";
import { AIAnalysisPanel } from "../pane/AIAnalysisPanel";
import { Context, PineTS } from "pinets";
import { DefaultTSer } from "../../timeseris/DefaultTSer";
import { TFrame } from "../../timeseris/TFrame";
import type { KlineKind } from "../plot/PlotKline";
import type { Plot } from "../plot/Plot";
import { fetchData, getMarket } from "../../domain/DataFecther";
import { fetchRealtime } from "../../domain/AShareData";

import {
    ActionButton,
    ActionButtonGroup,
    DialogTrigger,
    Divider,
    Popover,
    type Selection,
    ToggleButtonGroup,
    ToggleButton,
    Tooltip,
    TooltipTrigger,
    TagGroup,
    Tag
} from "@react-spectrum/s2";

import Line from '@react-spectrum/s2/icons/Line';
import Play from '@react-spectrum/s2/icons/Play';
import Edit from '@react-spectrum/s2/icons/Edit';
import Filter from '@react-spectrum/s2/icons/Filter';
import Copy from '@react-spectrum/s2/icons/Copy';
import Delete from '@react-spectrum/s2/icons/Delete';
import Properties from '@react-spectrum/s2/icons/Properties';
import AudioWave from '@react-spectrum/s2/icons/AudioWave';
import GridTypeLines from '@react-spectrum/s2/icons/GridTypeLines';
import LineHeight from '@react-spectrum/s2/icons/LineHeight';
import ChartTrend from '@react-spectrum/s2/icons/ChartTrend';
import Collection from '@react-spectrum/s2/icons/Collection';
import DistributeSpaceHorizontally from '@react-spectrum/s2/icons/DistributeSpaceHorizontally';
import EditNo from '@react-spectrum/s2/icons/EditNo';
import Erase from '@react-spectrum/s2/icons/Erase';
import Refresh from '@react-spectrum/s2/icons/Refresh';
import SelectNo from '@react-spectrum/s2/icons/SelectNo';
import SelectNone from '@react-spectrum/s2/icons/SelectNone';
import New from '@react-spectrum/s2/icons/New';
import Maximize from '@react-spectrum/s2/icons/Maximize';
import BrightnessContrast from '@react-spectrum/s2/icons/BrightnessContrast';
import Background from '@react-spectrum/s2/icons/Background';
import HelpCircle from '@react-spectrum/s2/icons/HelpCircle';
import AlignTop from '@react-spectrum/s2/icons/AlignTop';
import DistributeHorizontalCenter from '@react-spectrum/s2/icons/DistributeHorizontalCenter';
import MenuHamburger from '@react-spectrum/s2/icons/MenuHamburger';
import Prototyping from '@react-spectrum/s2/icons/Prototyping';
import Add from '@react-spectrum/s2/icons/Add';
import DirectSelect from '@react-spectrum/s2/icons/DirectSelect';
import DistributeSpaceVertically from '@react-spectrum/s2/icons/DistributeSpaceVertically';
import Resize from '@react-spectrum/s2/icons/Resize';
import StrokeWidth from '@react-spectrum/s2/icons/StrokeWidth';
import Percentage from '@react-spectrum/s2/icons/Percentage';
import StarFilled from '@react-spectrum/s2/icons/StarFilled';
import Star from '@react-spectrum/s2/icons/Star';
import Exposure from '@react-spectrum/s2/icons/Exposure';
import FullScreenExit from '@react-spectrum/s2/icons/FullScreenExit';


import { style } from '@react-spectrum/s2/style' with {type: 'macro'};
import { Screenshot } from "../pane/Screenshot";


type Props = {
    width: number,

    toggleColorTheme?: () => void
    colorTheme?: 'light' | 'dark'
    navigate?: (path: string) => void
    symbol?: string
}

type State = {
    updateEvent?: UpdateEvent;
    updateDrawing?: UpdateDrawing;

    mouseCursor?: JSX.Element;
    referCursor?: JSX.Element;

    overlayIndicators?: Indicator[];
    stackedIndicators?: Indicator[];

    overlayIndicatorLabels?: string[][];
    stackedIndicatorLabels?: string[][];

    referOverlayIndicatorLabels?: string[][];
    referStackedIndicatorLabels?: string[][];

    selectedIndicatorTags?: Selection;
    drawingIdsToCreate?: Selection;

    yKlineView: number;
    yVolumeView: number;
    yIndicatorViews: number;
    yAxisx: number;
    svgHeight: number;
    containerHeight: number;
    yCursorRange: number[];

    isLoaded: boolean;

    screenshot: HTMLCanvasElement;
    isAIPanelOpen?: boolean;
    aiPanelWidth?: number;
    toast?: { message: string, type: 'success' | 'error' | 'info' };
    apiStatus?: {
        backoff_active?: boolean;
        backoff_until?: string;
        last_error?: string;
        failure_count?: number;
    };
}

// const allIndTags = ['macd']
const allIndTags = ['sma', 'ema', 'bb', 'rsi', 'macd']

const TOOLTIP_DELAY = 500; // ms

class KlineViewContainer extends Component<Props, State> {
    width: number;

    symbol: string;
    tframe: TFrame;
    tzone: string;

    baseSer: TSer;
    kvar: TVar<Kline>;
    xc: ChartXControl;

    reloadDataTimeoutId = undefined;
    realtimeIntervalId = undefined;
    latestTime: number;
    reloadIntervalMs = 10000;
    lastReloadAt = 0;
    lastRealtimeAt = 0;

    predefinedPines: Map<string, string>;
    pines?: { pineName: string, pine: string }[];

    containerRef: React.RefObject<HTMLDivElement>;
    contentRef: React.RefObject<HTMLDivElement>;
    globalKeyboardListener = undefined
    isDragging: boolean;
    xDragStart: number;
    yDragStart: number;

    // geometry variables
    toolbarWidth = 64;
    hTitle = 60;
    hIndtags = 28;

    hKlineView = 400;
    hVolumeView = 100;
    hIndicatorView = 160;
    hAxisx = 40;
    hSpacing = 25;

    callbacks: CallbacksToContainer

    systemScheme: string;

    constructor(props: Props) {
        super(props);
        // Calculate initial width accounting for AI panel open by default
        const defaultAiPanelWidth = 500;
        const toolbarWidth = this.toolbarWidth;
        const resizeHandleWidth = 4; // AI panel is open by default
        this.width = props.width - defaultAiPanelWidth - toolbarWidth - resizeHandleWidth;

        this.containerRef = React.createRef();
        this.contentRef = React.createRef();

        // Init base series and kvar immediately so they are available for initial render
        const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        this.tzone = getMarket() === 'ashare' ? 'Asia/Shanghai' : localTz;
        // Default to a safe fallback symbol/timeframe until data loads
        this.tframe = TFrame.DAILY;
        this.symbol = 'AAPL'; // Default symbol, will be updated in componentDidMount
        this.baseSer = new DefaultTSer(this.tframe, this.tzone, 365);
        this.kvar = this.baseSer.varOf(KVAR_NAME) as TVar<Kline>;
        this.xc = new ChartXControl(this.baseSer, this.width - ChartView.AXISY_WIDTH);

        const geometry = this.#calcGeometry([]);
        this.state = {
            isLoaded: false,
            updateEvent: { type: 'chart', changed: 0 },
            updateDrawing: { isHidingDrawing: false },
            overlayIndicators: [],
            stackedIndicators: [],
            selectedIndicatorTags: new Set(['sma', 'ema', 'macd']),
            drawingIdsToCreate: new Set(),
            screenshot: undefined,
            isAIPanelOpen: true,
            aiPanelWidth: 500,
            ...geometry,
        }

        console.log("KlinerViewContainer created");

        this.setOverlayIndicatorLabels = this.setOverlayIndicatorLabels.bind(this)
        this.setStackedIndicatorLabels = this.setStackedIndicatorLabels.bind(this)
        this.setSelectedIndicatorTags = this.setSelectedIndicatorTags.bind(this)
        this.setDrawingIdsToCreate = this.setDrawingIdsToCreate.bind(this)

        this.backToOriginalChartScale = this.backToOriginalChartScale.bind(this)
        this.toggleCrosshairVisiable = this.toggleCrosshairVisiable.bind(this)
        this.toggleOnCalendarMode = this.toggleOnCalendarMode.bind(this)
        this.toggleKlineKind = this.toggleKlineKind.bind(this)
        this.toggleScalar = this.toggleScalar.bind(this)

        this.handleSymbolTimeframeChanged = this.handleSymbolTimeframeChanged.bind(this)
        this.handleTakeScreenshot = this.handleTakeScreenshot.bind(this)

        this.onGlobalKeyDown = this.onGlobalKeyDown.bind(this)
        this.onMouseUp = this.onMouseUp.bind(this)
        this.onMouseDown = this.onMouseDown.bind(this)
        this.onMouseMove = this.onMouseMove.bind(this)
        this.onMouseLeave = this.onMouseLeave.bind(this)
        this.onDoubleClick = this.onDoubleClick.bind(this)
        this.onDoubleClick = this.onDoubleClick.bind(this)
        this.onWheel = this.onWheel.bind(this)
        this.handleForceRefresh = this.handleForceRefresh.bind(this)

        this.callbacks = {
            updateOverlayIndicatorLabels: this.setOverlayIndicatorLabels,
            updateStackedIndicatorLabels: this.setStackedIndicatorLabels,
            updateDrawingIdsToCreate: this.setDrawingIdsToCreate,
        }

        this.toggleAIPanel = this.toggleAIPanel.bind(this);
        this.handleResizeMouseDown = this.handleResizeMouseDown.bind(this);
        this.handleResizeMouseMove = this.handleResizeMouseMove.bind(this);
        this.handleResizeMouseUp = this.handleResizeMouseUp.bind(this);
    }

    toggleAIPanel() {
        this.setState(prev => ({ isAIPanelOpen: !prev.isAIPanelOpen }), () => {
            this.updateChartWidth();
        });
    }

    updateChartWidth() {
        const toolbarWidth = this.toolbarWidth;
        const resizeHandleWidth = this.state.isAIPanelOpen ? 4 : 0;
        const panelWidth = this.state.isAIPanelOpen ? (this.state.aiPanelWidth || 350) : 0;
        const newChartWidth = this.props.width - panelWidth - toolbarWidth - resizeHandleWidth;

        if (newChartWidth !== this.width) {
            this.width = newChartWidth;
            // Re-init chart x control with new width
            this.xc = new ChartXControl(this.baseSer, this.width - ChartView.AXISY_WIDTH);
            this.update({ type: 'chart' });
        }
    }

    // Resize Logic
    isResizing = false;

    handleResizeMouseDown(e: React.MouseEvent) {
        e.preventDefault();
        this.isResizing = true;
        document.addEventListener('mousemove', this.handleResizeMouseMove);
        document.addEventListener('mouseup', this.handleResizeMouseUp);
    }

    handleResizeMouseMove(e: MouseEvent) {
        if (!this.isResizing) return;

        if (this.containerRef.current) {
            const newWidth = document.body.clientWidth - e.clientX;
            // Constrain
            if (newWidth > 200 && newWidth < 800) {
                this.setState({ aiPanelWidth: newWidth }, () => {
                    this.updateChartWidth();
                });
            }
        }
    }

    handleResizeMouseUp() {
        this.isResizing = false;
        document.removeEventListener('mousemove', this.handleResizeMouseMove);
        document.removeEventListener('mouseup', this.handleResizeMouseUp);
    }

    handleForceRefresh = async () => {
        try {
            this.setState({ toast: { message: "Syncing data...", type: 'info' } });

            const baseUrl = import.meta.env.VITE_ASHARE_API_URL || "";
            const res = await fetch(`${baseUrl}/api/sync/${this.symbol}?period=${this.tframe.shortName}`, {
                method: 'POST'
            });
            const data = await res.json();

            // Also sync fundamentals for current symbol
            try {
                const fundamentalsRes = await fetch(`${baseUrl}/api/fundamentals/sync`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbols: [this.symbol], force: true })
                });
                await fundamentalsRes.json();
            } catch (e) {
                // Ignore fundamentals sync errors for now
            }

            if (data.success) {
                if (data.api_status) {
                    this.setState({ apiStatus: data.api_status });
                }
                const source = data.source || "unknown";
                const count = data.count || 0;
                this.setState({
                    toast: {
                        message: `Success: Synced ${count} records from ${source}`,
                        type: 'success'
                    }
                });
                // Clear toast after 3 seconds
                setTimeout(() => this.setState({ toast: undefined }), 3000);
            } else {
                throw new Error(data.detail || "Unknown error");
            }

            // Reload chart data (reuse logic from handleSymbolTimeframeChanged)
            this.handleSymbolTimeframeChanged(this.symbol, this.tframe);

        } catch (e: any) {
            console.error("Force sync failed", e);
            this.setState({
                toast: {
                    message: `Sync Failed: ${e.message || e}`,
                    type: 'error'
                }
            });
            setTimeout(() => this.setState({ toast: undefined }), 5000);
        }
    }

    handleSyncWatchlistDaily = async () => {
        try {
            this.setState({ toast: { message: "Syncing watchlist daily data...", type: 'info' } });

            const baseUrl = import.meta.env.VITE_ASHARE_API_URL || "";
            const res = await fetch(`${baseUrl}/api/watchlist/sync_daily?market=ashare`, {
                method: 'POST'
            });
            const data = await res.json();

            if (data.success) {
                if (data.api_status) {
                    this.setState({ apiStatus: data.api_status });
                }
                const total = data.total || 0;
                const ok = data.success_count ?? 0;
                const fail = data.error_count ?? 0;
                const message = total === 0
                    ? "No A-share watchlist items to sync."
                    : `Watchlist synced: ${ok}/${total}${fail ? ` (failed ${fail})` : ''}`;
                this.setState({
                    toast: {
                        message,
                        type: fail ? 'error' : 'success'
                    }
                });
                setTimeout(() => this.setState({ toast: undefined }), fail ? 5000 : 3000);

                // Refresh current chart in case the active symbol is in watchlist
                this.handleSymbolTimeframeChanged(this.symbol, this.tframe);
            } else {
                throw new Error(data.detail || "Unknown error");
            }
        } catch (e: any) {
            console.error("Watchlist sync failed", e);
            this.setState({
                toast: {
                    message: `Sync Failed: ${e.message || e}`,
                    type: 'error'
                }
            });
            setTimeout(() => this.setState({ toast: undefined }), 5000);
        }
    }

    fetchOPredefinedPines = (pineName: string[]) => {
        const baseUrl = import.meta.env.BASE_URL || "/";
        const assetBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
        const fetchIndicatorFn = (pineName: string) => {
            const url = `${assetBase}indicators/${pineName}.pine`;
            return fetch(url)
                .then(r => {
                    if (!r.ok) {
                        throw new Error(`Failed to fetch indicator ${pineName}: ${r.status}`);
                    }
                    return r.text();
                })
                .then(pine => ({ pineName, pine }))
                .catch(error => {
                    console.warn(`[fetchOPredefinedPines] ${pineName} failed`, error);
                    return { pineName, pine: undefined };
                })
        }

        return Promise.all(pineName.map(pineName => fetchIndicatorFn(pineName)))
    }

    getSelectedIncicators = () => {
        let selectedIndicatorFns = new Map<string, string>();

        const selectedIndicatorTagsNow = this.state.selectedIndicatorTags
        if (selectedIndicatorTagsNow === 'all') {
            selectedIndicatorFns = this.predefinedPines;

        } else {
            for (const pineName of selectedIndicatorTagsNow) {
                selectedIndicatorFns.set(pineName as string, this.predefinedPines.get(pineName as string))
            }
        }

        return Array.from(selectedIndicatorFns, ([pineName, pine]) => ({ pineName, pine }))
    }

    fetchData_calcPines = async (startTime: number, limit: number) => {

        const symbol = this.symbol
        console.log(`[KlineViewContainer] fetchData_calcPines: Fetching for ${symbol}`);
        const tframe = this.tframe
        const tzone = this.tzone
        const baseSer = this.baseSer
        const kvar = this.kvar
        const xc = this.xc

        const pines = this.pines || this.getSelectedIncicators()

        return fetchData(baseSer, symbol, tframe, tzone, startTime, limit).then(latestTime => {
            let start = performance.now()

            const rawData = kvar.toArray();
            const hasData = rawData.some(k => k !== undefined);
            const baseChanged = (this.state.updateEvent?.changed ?? 0) + 1;

            this.latestTime = latestTime;
            // Always reinit xc to get correct last occurred time/row
            console.log("reinit xc")
            xc.reinit()

            this.updateState({
                isLoaded: true,
                updateEvent: { type: 'chart', changed: baseChanged },
                overlayIndicators: this.state.overlayIndicators || [],
                stackedIndicators: this.state.stackedIndicators || [],
            });

            if (!hasData) {
                this.setState({
                    toast: { message: `No data for ${symbol}`, type: 'info' }
                });
                return;
            }

            // console.log(kvar.toArray().filter(k => k === undefined), "undefined klines in series");
            const pinets = new PineTS(rawData, symbol, tframe.shortName);

            pinets.ready()
                .then(async () => {
                    const fnRuns: Promise<{ pineName: string, result: Context }>[] = []
                    for (const { pineName, pine } of pines) {
                        if (pine !== undefined) {
                            const fnRun = pinets.run(pine)
                                .then(result => ({ pineName, result }))
                                .catch(error => {
                                    console.error(error);

                                    return { pineName, result: undefined }
                                })

                            fnRuns.push(fnRun)
                        }
                    }

                    return Promise.all(fnRuns).then(results => {
                        console.log(`indicators calculated in ${performance.now() - start} ms`);

                        start = performance.now();

                        const overlayIndicators = [];
                        const stackedIndicators = [];

                        results.map(({ pineName, result }, n) => {
                            if (result) {
                                const tvar = baseSer.varOf(pineName) as TVar<unknown[]>;
                                const size = baseSer.size();
                                const indicator = result.indicator;
                                const plots = Object.values(result.plots) as Plot[];
                                const dataValues = plots.map(({ data }) => data);
                                for (let i = 0; i < size; i++) {
                                    const vs = dataValues.map(v => v[i].value);
                                    tvar.setByIndex(i, vs);
                                }

                                const outputs = plots.map(({ title, options }, atIndex) => {
                                    return ({ atIndex, title, options })
                                })

                                const overlay = indicator !== undefined && indicator.overlay
                                if (overlay) {
                                    overlayIndicators.push({ pineName, tvar, outputs })

                                } else {
                                    stackedIndicators.push({ pineName, tvar, outputs })
                                }
                            }
                        })

                        const indicatorChanged = baseChanged + 1;
                        this.updateState({
                            updateEvent: { type: 'chart', changed: indicatorChanged },
                            overlayIndicators,
                            stackedIndicators,
                        })

                        if (latestTime !== undefined) {
                            this.lastReloadAt = Date.now();
                            this.scheduleNextReload(latestTime);
                        }

                    })
                })
                .catch(err => {
                    console.error("Failed to fetch/calculate", err);
                    this.setState({
                        toast: { message: `Load failed: ${err.message || 'Unknown error'}`, type: 'error' },
                        isLoaded: true // Ensure loaded state so we don't stick on loading
                    });
                })

        }).catch(err => {
            console.error("[KlineViewContainer] fetchData failed", err);
            this.setState({
                toast: { message: `Load failed: ${err.message || 'Unknown error'}`, type: 'error' },
                isLoaded: true
            });
        })
    }

    fetchRealtimeQuote = async () => {
        if (getMarket() !== 'ashare') return;
        if (typeof document !== 'undefined' && document.hidden) return;
        if (!this.symbol) return;

        if (!this.isRealtimeWindow()) return;

        const now = Date.now();
        if (this.lastRealtimeAt && now - this.lastRealtimeAt < this.reloadIntervalMs) {
            return;
        }
        this.lastRealtimeAt = now;

        try {
            const rt = await fetchRealtime(this.symbol);
            if (!rt || rt.price === undefined || !Number.isFinite(rt.price) || rt.price <= 0) return;
            const stale = (rt as unknown as { stale?: boolean }).stale;
            if (stale) return;

            const isDaily = this.tframe.shortName === '1d' || this.tframe.shortName === '1D';
            const kvar = this.kvar;
            const size = kvar.values().size();
            if (size <= 0) return;
            const last = kvar.getByIndex(size - 1);
            if (!last) return;

            const formatter = new Intl.DateTimeFormat('en-CA', {
                timeZone: this.tzone,
                year: 'numeric',
                month: '2-digit',
                day: '2-digit'
            });
            const lastDate = formatter.format(new Date(last.time));
            const quoteDate = formatter.format(new Date(rt.timestamp || now));
            if (lastDate !== quoteDate) {
                if (!isDaily) return;
                const open = Number.isFinite(rt.open) ? rt.open : rt.price;
                const high = Number.isFinite(rt.high) ? rt.high : Math.max(open, rt.price);
                const low = Number.isFinite(rt.low) ? rt.low : Math.min(open, rt.price);
                const volume = Number.isFinite(rt.volume) ? rt.volume : 0;
                const ts = rt.timestamp || now;
                const kline = new Kline(ts, open, high, low, rt.price, volume, ts, false);
                this.baseSer.addToVar(KVAR_NAME, kline);
                this.xc.reinit();
                const changed = (this.state.updateEvent?.changed ?? 0) + 1;
                this.updateState({ updateEvent: { type: 'chart', changed } });
                return;
            }

            const high = Number.isFinite(rt.high) ? rt.high : rt.price;
            const low = Number.isFinite(rt.low) ? rt.low : rt.price;
            last.close = rt.price;
            if (high > last.high) last.high = high;
            if (low < last.low) last.low = low;
            if (isDaily) {
                if (Number.isFinite(rt.volume)) {
                    last.volume = Math.max(last.volume, rt.volume);
                }
            }
            last.closeTime = rt.timestamp || now;
            last.isClosed = false;

            kvar.setByIndex(size - 1, last);
            const changed = (this.state.updateEvent?.changed ?? 0) + 1;
            this.updateState({ updateEvent: { type: 'chart', changed } });
        } catch (e) {
            // ignore realtime errors
        }
    }

    isRealtimeWindow() {
        try {
            const now = new Date();
            const cnNow = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Shanghai" }));
            const day = cnNow.getDay();
            if (day === 0 || day === 6) return false;
            const minutes = cnNow.getHours() * 60 + cnNow.getMinutes();
            const inMorning = minutes >= (9 * 60 + 30) && minutes <= (11 * 60 + 30);
            const inAfternoon = minutes >= (13 * 60) && minutes <= (15 * 60);
            const postClose = minutes > (15 * 60) && minutes <= (15 * 60 + 5);
            return inMorning || inAfternoon || postClose;
        } catch {
            return true;
        }
    }

    scheduleNextReload(latestTime?: number) {
        if (this.reloadDataTimeoutId) {
            clearTimeout(this.reloadDataTimeoutId);
            this.reloadDataTimeoutId = undefined;
        }
        if (latestTime === undefined) return;
        if (typeof document !== 'undefined' && document.hidden) return;
        const now = Date.now();
        const elapsed = this.lastReloadAt ? now - this.lastReloadAt : this.reloadIntervalMs;
        const delay = Math.max(0, this.reloadIntervalMs - elapsed);
        this.reloadDataTimeoutId = setTimeout(() => {
            if (typeof document !== 'undefined' && document.hidden) return;
            this.fetchData_calcPines(latestTime, 1000);
        }, delay);
    }

    startRealtimePolling() {
        if (this.realtimeIntervalId) {
            clearInterval(this.realtimeIntervalId);
            this.realtimeIntervalId = undefined;
        }
        if (typeof document !== 'undefined' && document.hidden) return;
        this.fetchRealtimeQuote();
        this.realtimeIntervalId = setInterval(() => {
            if (typeof document !== 'undefined' && document.hidden) return;
            this.fetchRealtimeQuote();
        }, this.reloadIntervalMs);
    }

    stopRealtimePolling() {
        if (this.realtimeIntervalId) {
            clearInterval(this.realtimeIntervalId);
            this.realtimeIntervalId = undefined;
        }
    }

    override componentDidMount() {
        window.addEventListener('akshare-api-status', this.onAkshareStatus);
        document.addEventListener('visibilitychange', this.onVisibilityChange);
        this.fetchOPredefinedPines(allIndTags)
            .then(pines => {
                this.predefinedPines = new Map(pines.map(p => [p.pineName, p.pine]))
            })
            .then(async () => {
                // 根据市场类型设置默认 symbol
                const { getMarket } = await import("../../domain/DataFecther");
                const { getDefaultSymbol } = await import("../../domain/Watchlist");
                const market = getMarket();

                // Restore last selected symbol from sessionStorage to avoid cross-tab interference.
                // If provided via props, use that. Otherwise use sessionStorage, then localStorage as fallback.
                const lastSymbol = this.props.symbol
                    || sessionStorage.getItem('last_selected_symbol')
                    || localStorage.getItem('last_selected_symbol');
                this.symbol = lastSymbol || getDefaultSymbol(market);
                // this.tframe = TFrame.DAILY // Already set in constructor
                // this.tzone = Intl.DateTimeFormat().resolvedOptions().timeZone; // Already set in constructor

                // this.baseSer = new DefaultTSer(this.tframe, this.tzone, 365); // Already set in constructor
                // this.kvar = this.baseSer.varOf(KVAR_NAME) as TVar<Kline>; // Already set in constructor
                // this.xc = new ChartXControl(this.baseSer, this.width - ChartView.AXISY_WIDTH); // Already set in constructor

                this.fetchData_calcPines(undefined, 365).then(() => {
                    this.lastReloadAt = Date.now();
                    this.startRealtimePolling();
                    this.globalKeyboardListener = this.onGlobalKeyDown;
                    document.addEventListener("keydown", this.onGlobalKeyDown);
                    window.addEventListener('vibetrader-symbol-change', this.onCustomSymbolChange);

                    if (this.containerRef.current) {
                        this.containerRef.current.focus()
                    }
                })

            })
    }

    override componentDidUpdate(prevProps: Props) {
        console.log(`[KlineViewContainer] componentDidUpdate: prop symbol ${prevProps.symbol} -> ${this.props.symbol} vs current ${this.symbol}`);
        if (this.props.symbol && this.props.symbol !== prevProps.symbol && this.props.symbol !== this.symbol) {
            this.handleSymbolTimeframeChanged(this.props.symbol, this.tframe);
        }
    }

    onCustomSymbolChange = (e: Event) => {
        const detail = (e as CustomEvent).detail;
        if (detail && detail.symbol && detail.symbol !== this.symbol) {
            console.log("Custom event: symbol changed to", detail.symbol);
            this.handleSymbolTimeframeChanged(detail.symbol, this.tframe);
        }
    }

    onAkshareStatus = (e: Event) => {
        const detail = (e as CustomEvent).detail;
        if (detail) {
            this.setState({ apiStatus: detail });
        }
    }

    onVisibilityChange = () => {
        if (typeof document !== 'undefined' && document.hidden) {
            if (this.reloadDataTimeoutId) {
                clearTimeout(this.reloadDataTimeoutId);
                this.reloadDataTimeoutId = undefined;
            }
            this.stopRealtimePolling();
            return;
        }
        const now = Date.now();
        const elapsed = this.lastReloadAt ? now - this.lastReloadAt : this.reloadIntervalMs;
        if (elapsed >= this.reloadIntervalMs) {
            this.fetchData_calcPines(this.latestTime, 1000);
            this.startRealtimePolling();
            return;
        }
        this.scheduleNextReload(this.latestTime);
        this.startRealtimePolling();
    }

    override componentWillUnmount() {
        if (this.reloadDataTimeoutId) {
            clearTimeout(this.reloadDataTimeoutId);
        }
        this.stopRealtimePolling();

        if (this.globalKeyboardListener) {
            document.removeEventListener("keydown", this.onGlobalKeyDown)
        }
        document.removeEventListener('visibilitychange', this.onVisibilityChange);
        window.removeEventListener('vibetrader-symbol-change', this.onCustomSymbolChange);
        window.removeEventListener('akshare-api-status', this.onAkshareStatus);
    }

    update(event: UpdateEvent) {
        const changed = this.state.updateEvent.changed + 1;
        this.updateState({ updateEvent: { ...event, changed } });
    }

    updateState(newState: Partial<State>) {
        const xc = this.xc;

        let referCursor: JSX.Element
        let mouseCursor: JSX.Element
        if (xc.isReferCursorEnabled) {
            const time = xc.tr(xc.referCursorRow)
            if (xc.occurred(time)) {
                const cursorX = xc.xr(xc.referCursorRow)
                referCursor = this.#plotCursor(cursorX, 'annot-refer')
            }
        }

        if (xc.isMouseCursorEnabled) {
            const cursorX = xc.xr(xc.mouseCursorRow)
            mouseCursor = this.#plotCursor(cursorX, 'annot-mouse')
        }

        // need to re-calculate geometry?
        const geometry = newState.stackedIndicators
            ? this.#calcGeometry(newState.stackedIndicators)
            : undefined

        this.setState({ ...(newState as (Pick<State, keyof State> | State)), ...geometry, referCursor, mouseCursor })
    }

    #calcGeometry(stackedIndicators: Indicator[]) {
        stackedIndicators = stackedIndicators || [];

        const yKlineView = this.hSpacing;
        const yVolumeView = yKlineView + this.hKlineView + this.hSpacing;
        const yIndicatorViews = yVolumeView + this.hVolumeView + this.hSpacing;
        const yAxisx = yIndicatorViews + stackedIndicators.length * (this.hIndicatorView + this.hSpacing);

        const svgHeight = yAxisx + this.hAxisx;
        const containerHeight = svgHeight + this.hTitle + this.hIndtags;
        const yCursorRange = [0, yAxisx];

        return { yKlineView, yVolumeView, yIndicatorViews, yAxisx, svgHeight, containerHeight, yCursorRange }
    }

    #indicatorViewId(n: number) {
        return 'indicator-' + n;
    }

    #calcXYMouses(x: number, y: number) {
        if (y >= this.state.yKlineView && y < this.state.yKlineView + this.hKlineView) {
            return { who: 'kline', x, y: y - this.state.yKlineView };

        } else if (y >= this.state.yVolumeView && y < this.state.yVolumeView + this.hVolumeView) {
            return { who: 'volume', x, y: y - this.state.yVolumeView };

        } else if (y > this.state.yAxisx && y < this.state.yAxisx + this.hAxisx) {
            return { who: 'axisx', x, y: y - this.state.yVolumeView };

        } else {
            if (this.state.stackedIndicators) {
                for (let n = 0; n < this.state.stackedIndicators.length; n++) {
                    const yIndicatorView = this.state.yIndicatorViews + n * (this.hIndicatorView + this.hSpacing);
                    if (y >= yIndicatorView && y < yIndicatorView + this.hIndicatorView) {
                        return { who: this.#indicatorViewId(n), x, y: y - yIndicatorView };
                    }
                }
            }
        }

        return undefined;
    }

    #plotCursor(x: number, className: string) {
        if (this.state.drawingIdsToCreate === 'all' || this.state.drawingIdsToCreate.size > 0 || this.xc.isCrosshairEnabled) {
            return <></>
        }

        const crosshair = new Path;
        // vertical line
        crosshair.moveto(x, this.state.yCursorRange[0]);
        crosshair.lineto(x, this.state.yCursorRange[1])

        return (
            <g className={className}>
                {crosshair.render()}
            </g>
        )
    }

    isNotInAxisYArea(x: number) {
        return x < this.width - ChartView.AXISY_WIDTH
    }

    translate(e: React.MouseEvent) {
        const rect = e.currentTarget.getBoundingClientRect();
        const scrollTop = this.contentRef.current?.scrollTop || 0;
        // Adjust for Left Toolbar and Header (Title + TagGroup)
        return [
            e.clientX - rect.left - this.toolbarWidth,
            e.clientY - rect.top - this.hTitle - this.hIndtags + scrollTop
        ]
    }

    onGlobalKeyDown(e: KeyboardEvent) {
        if (
            document.activeElement.tagName === 'INPUT' ||
            document.activeElement.tagName === 'TEXTAREA'
        ) {
            return;
        }

        const xc = this.xc;
        xc.isMouseCursorEnabled = false;

        const fastSteps = Math.floor(xc.nBars * 0.168)

        switch (e.key) {
            case "ArrowLeft":
                if (e.ctrlKey) {
                    xc.moveCursorInDirection(fastSteps, -1)

                } else {
                    xc.moveChartsInDirection(fastSteps, -1)
                }

                this.update({ type: 'chart' })
                break;

            case "ArrowRight":
                if (e.ctrlKey) {
                    xc.moveCursorInDirection(fastSteps, 1)

                } else {
                    xc.moveChartsInDirection(fastSteps, 1)
                }

                this.update({ type: 'chart' })
                break;

            case "ArrowUp":
                if (!e.ctrlKey) {
                    xc.growWBar(1)
                    this.update({ type: 'chart' })
                }
                break;

            case "ArrowDown":
                if (!e.ctrlKey) {
                    xc.growWBar(-1);
                    this.update({ type: 'chart' })
                }
                break;

            case " ":
                xc.isCursorAccelerated = !xc.isCursorAccelerated
                break;

            case "Escape":
                if (xc.selectedDrawingIdx !== undefined) {
                    this.setState({ updateDrawing: { ...(this.state.updateDrawing), action: 'unselect' } })

                } else {
                    xc.isReferCursorEnabled = !xc.isReferCursorEnabled;

                    this.update({ type: 'cursors' })
                }
                break;

            case 'Delete':
                this.setState({ updateDrawing: { ...(this.state.updateDrawing), action: 'delete' } })
                break;

            default:
        }
    }


    onMouseLeave() {
        const xc = this.xc;

        // clear mouse cursor
        xc.isMouseCursorEnabled = false;

        this.update({ type: 'cursors' });
    }

    onMouseDown(e: React.MouseEvent) {
        this.isDragging = true

        const [x, y] = this.translate(e)
        this.xDragStart = x;
        this.yDragStart = y;
    }

    onMouseMove(e: React.MouseEvent) {
        const xc = this.xc;
        const [x, y] = this.translate(e)

        if (this.isDragging && xc.mouseDownHitDrawingIdx === undefined) {
            // drag chart
            const dx = x - this.xDragStart
            const dy = y - this.yDragStart
            const nBarDelta = Math.ceil(dx / xc.wBar)

            xc.isMouseCursorEnabled = false
            xc.isReferCursorEnabled = false
            xc.moveChartsInDirection(nBarDelta, -1, true)

            // reset to current position
            this.xDragStart = x;
            this.yDragStart = y;

            if (e.ctrlKey) {
                // notice chart view to zoom in / out
                this.update({ type: 'chart', deltaMouse: { dx, dy } });

            } else {
                this.update({ type: 'chart' });
            }

            // NOTE cursor shape will always be processed in ChartView's onDrawingMouseMove

            return
        }

        if (this.state.drawingIdsToCreate === 'all' || this.state.drawingIdsToCreate.size > 0 || xc.selectedDrawingIdx !== undefined || xc.mouseMoveHitDrawingIdx !== undefined) {
            // is under drawing?
            xc.isMouseCursorEnabled = false;
            this.update({ type: 'cursors' });
            return
        }

        const b = xc.bx(x);

        if (this.isNotInAxisYArea(x)) {
            // show mouse cursor only when x is not in the axis-y area
            const row = xc.rb(b)
            xc.setMouseCursorByRow(row)
            xc.isMouseCursorEnabled = true

        } else {
            xc.isMouseCursorEnabled = false;
        }

        const xyMouse = this.#calcXYMouses(x, y);

        this.update({ type: 'cursors', xyMouse });
    }

    onMouseUp(e: React.MouseEvent) {
        if (this.isDragging) {
            this.isDragging = false
            this.xDragStart = undefined
            this.yDragStart = undefined
        }
    }

    onDoubleClick(e: React.MouseEvent) {
        const xc = this.xc;
        const [x, y] = this.translate(e)

        // set refer cursor
        if (this.isNotInAxisYArea(x)) {
            const time = xc.tx(x);
            if (!xc.occurred(time)) {
                return;
            }

            // align x to bar center
            const b = xc.bx(x);

            // draw refer cursor only when not in the axis-y area
            if (
                y >= this.state.yCursorRange[0] && y <= this.state.svgHeight &&
                b >= 1 && b <= xc.nBars
            ) {
                const row = xc.rb(b)
                xc.setReferCursorByRow(row, true)
                xc.isReferCursorEnabled = true;

                this.update({ type: 'cursors' });
            }

        } else {
            xc.isReferCursorEnabled = false;

            this.update({ type: 'cursors' });
        }
    }

    onWheel(e: React.WheelEvent) {
        const xc = this.xc;

        const deltaX = e.deltaX || 0
        const deltaY = e.deltaY || 0
        const absX = Math.abs(deltaX)
        const absY = Math.abs(deltaY)
        const isPixelMode = e.deltaMode === 0x00

        if (!e.shiftKey && !e.ctrlKey && isPixelMode && absY > absX) {
            // Trackpad vertical scroll: ignore to avoid horizontal jitter.
            return
        }

        if (absX > 0) {
            // Prevent browser back/forward navigation on horizontal trackpad swipe.
            e.preventDefault()
            e.stopPropagation()
        }

        const dominant = absX > absY ? deltaX : deltaY
        const delta = Math.sign(dominant)
        if (!delta) {
            return
        }

        // treating one event as 'one unit' is good enough and safer.
        switch (e.deltaMode) {
            case 0x00:  // The delta values are specified in pixels.
                break;

            case 0x01: // The delta values are specified in lines.
                break;

            case 0x02: // The delta values are specified in pages.
                break;
        }

        if (e.shiftKey) {
            // zoom in / zoom out
            xc.growWBar(-Math.sign(delta))

        } else if (e.ctrlKey) {
            const fastSteps = Math.floor(xc.nBars * 0.168)
            const unitsToScroll = xc.isCursorAccelerated ? delta * fastSteps : delta;
            // move refer cursor left / right
            xc.scrollReferCursor(unitsToScroll, true)

        } else {
            const fastSteps = Math.floor(xc.nBars * 0.168)
            const unitsToScroll = xc.isCursorAccelerated ? delta * fastSteps : delta;
            // keep referCursor staying same x in screen, and move
            xc.scrollChartsHorizontallyByBar(unitsToScroll)
        }

        this.update({ type: 'chart' });
    }

    setOverlayIndicatorLabels(vs: string[][], refVs?: string[][]) {
        let overlayIndicatorLabels = this.state.overlayIndicatorLabels
        let referOverlayIndicatorLabels = this.state.referOverlayIndicatorLabels

        const nOverlayInds = this.state.overlayIndicators.length

        overlayIndicatorLabels = overlayIndicatorLabels || new Array(nOverlayInds)
        referOverlayIndicatorLabels = referOverlayIndicatorLabels || new Array(nOverlayInds)

        for (let n = 0; n < nOverlayInds; n++) {
            overlayIndicatorLabels[n] = vs[n];
            referOverlayIndicatorLabels[n] = refVs[n];
        }

        this.setState({ overlayIndicatorLabels, referOverlayIndicatorLabels })
    }


    setStackedIndicatorLabels_old(n: number) {
        return (vs: string[], refVs?: string[]) => {
            let stackedIndicatorLabels = this.state.stackedIndicatorLabels
            let referStackedIndicatorLabels = this.state.referStackedIndicatorLabels

            stackedIndicatorLabels = stackedIndicatorLabels || new Array(this.state.stackedIndicators.length)
            referStackedIndicatorLabels = referStackedIndicatorLabels || new Array(this.state.stackedIndicators.length)

            stackedIndicatorLabels[n] = vs;
            referStackedIndicatorLabels[n] = refVs;

            this.setState({ stackedIndicatorLabels, referStackedIndicatorLabels })
        }
    }

    setStackedIndicatorLabels(n: number, vs: string[], refVs?: string[]) {
        let stackedIndicatorLabels = this.state.stackedIndicatorLabels
        let referStackedIndicatorLabels = this.state.referStackedIndicatorLabels

        stackedIndicatorLabels = stackedIndicatorLabels || new Array(this.state.stackedIndicators.length)
        referStackedIndicatorLabels = referStackedIndicatorLabels || new Array(this.state.stackedIndicators.length)

        stackedIndicatorLabels[n] = vs;
        referStackedIndicatorLabels[n] = refVs;

        this.setState({ stackedIndicatorLabels, referStackedIndicatorLabels })
    }

    setSelectedIndicatorTags(selectedIndicatorTags: Selection) {
        if (this.reloadDataTimeoutId) {
            clearTimeout(this.reloadDataTimeoutId);
        }

        return new Promise<void>((resolve) => {
            this.setState(
                { selectedIndicatorTags },
                () =>
                    this.fetchData_calcPines(this.latestTime, 1000).then(() => {
                        resolve();
                    }))
        })
    }

    setDrawingIdsToCreate(ids?: Selection) {
        if (ids === undefined || ids !== 'all' && ids.size === 0) {
            this.setState({
                updateDrawing: {
                    ...(this.state.updateDrawing),
                    createDrawingId: undefined
                },
                drawingIdsToCreate: new Set()
            })

        } else {
            const [drawingId] = ids
            this.setState({
                updateDrawing: {
                    ...(this.state.updateDrawing),
                    action: 'create',
                    createDrawingId: drawingId as string
                },
                drawingIdsToCreate: ids
            })
        }
    }

    backToOriginalChartScale() {
        this.update({ type: 'chart', deltaMouse: { dx: undefined, dy: undefined } });
    }

    toggleCrosshairVisiable() {
        const xc = this.xc;

        xc.isCrosshairEnabled = !xc.isCrosshairEnabled;

        this.update({ type: 'cursors' });
    }

    toggleOnCalendarMode() {
        const xc = this.xc;

        // toggle onCalendarMode
        xc.isOnCalendarMode = !xc.isOnCalendarMode;
        // set default wBar
        xc.wBar = 10;

        xc.reinit();

        this.update({ type: 'chart' });
    }

    toggleKlineKind() {
        this.update({ type: 'chart', klineKind: 'toggle' });
    }

    toggleScalar() {
        this.update({ type: 'chart', scalarMode: 'toggle' });
    }

    handleSymbolTimeframeChanged = (symbol: string, tframe?: TFrame | string) => {
        console.log(`[KlineViewContainer] handleSymbolTimeframeChanged: ${this.symbol} -> ${symbol}, tframe: ${tframe}`);
        // Cancel any pending data reload
        if (this.reloadDataTimeoutId) {
            clearTimeout(this.reloadDataTimeoutId);
            this.reloadDataTimeoutId = undefined;
        }
        this.lastRealtimeAt = 0;

        this.symbol = symbol;
        sessionStorage.setItem('last_selected_symbol', symbol);
        localStorage.setItem('last_selected_symbol', symbol);

        // Update timeframe if provided (string shortName or TFrame instance)
        if (tframe !== undefined) {
            if (typeof tframe === 'string') {
                const parsed = TFrame.PREDEFINED.find((tf) => tf.shortName === tframe) || TFrame.ofName(tframe);
                if (parsed) {
                    this.tframe = parsed;
                } else {
                    console.warn(`[KlineViewContainer] Unknown timeframe: ${tframe}, keep current.`);
                }
            } else {
                this.tframe = tframe;
            }
        }

        // Force UI update to show new symbol immediately
        this.update({ type: 'chart' });

        // Update timezone based on market before rebuilding series
        const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        this.tzone = getMarket() === 'ashare' ? 'Asia/Shanghai' : localTz;

        // Re-create baseSer with new timeframe to clear old data
        this.baseSer = new DefaultTSer(this.tframe, this.tzone, 365);
        this.kvar = this.baseSer.varOf(KVAR_NAME) as TVar<Kline>;

        // Force re-init xc with new baseSer
        this.xc = new ChartXControl(this.baseSer, this.width - ChartView.AXISY_WIDTH);

        // Critical: Update state with new kvar/xc to ensure child components re-render with new data references
        // We do this via forceUpdate or setState since these are not in state
        this.forceUpdate();

        // Fetch new data
        this.fetchData_calcPines(undefined, 365).then(() => {
            this.startRealtimePolling();
        });
    }

    handleTakeScreenshot() {
        // html2canvas(document.body).then(canvas => {
        //     document.body.appendChild(canvas)
        // });

        const node = this.containerRef.current
        html2canvas(node).then(canvas => {
            // document.body.appendChild(canvas)
            // const img = canvas.toDataURL("image/png");
            // document.write('<img src="' + img + '"/>');

            this.setState({ screenshot: canvas })
        });
    }

    render() {
        const apiStatus = this.state.apiStatus;
        let apiBannerMessage: string | undefined;
        if (apiStatus?.backoff_active && apiStatus.backoff_until) {
            const until = new Date(apiStatus.backoff_until);
            const remainingMs = until.getTime() - Date.now();
            const remainingMin = Math.max(0, Math.ceil(remainingMs / 60000));
            const untilText = Number.isNaN(until.getTime())
                ? "unknown time"
                : until.toLocaleTimeString("en-US", { hour12: false });
            apiBannerMessage = `API degraded. Backoff until ${untilText} (~${remainingMin}m). Click Sync Latest Data to force.`;
        } else if (apiStatus?.backoff_active) {
            apiBannerMessage = "API degraded. Backoff active. Click Sync Latest Data to force.";
        }

        return (
            <div
                ref={this.containerRef}
                style={{
                    width: '100%',
                    height: '100vh',
                    position: 'relative',
                    outline: 'none',
                    display: 'flex',
                    flexDirection: 'row',
                    overflow: 'hidden',
                    overscrollBehaviorX: 'none'
                }}
                tabIndex={-1}
                // onKeyDown={this.onKeyDown}
                // onKeyUp={this.onKeyUp}

                onMouseLeave={this.onMouseLeave}
                onMouseDown={this.onMouseDown}
                onMouseMove={this.onMouseMove}
                onMouseUp={this.onMouseUp}
                onDoubleClick={this.onDoubleClick}
                onWheel={this.onWheel}
            >
                {/* Left Toolbar */}
                <div style={{
                    width: this.toolbarWidth,
                    minWidth: this.toolbarWidth,
                    height: '100%',
                    borderRight: '1px solid #e0e0e0',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    padding: '4px 0',
                    gap: '2px',
                    backgroundColor: '#f8f9fa',
                    zIndex: 10,
                    overflowY: 'auto',
                    overflowX: 'hidden'
                }}
                    className="left-toolbar"
                    onMouseDown={(e) => e.stopPropagation()}
                >

                    <ActionButtonGroup orientation="vertical" density="compact">

                        <ToggleButtonGroup
                            orientation="vertical"
                            selectionMode="multiple"
                            selectedKeys={this.state.drawingIdsToCreate}
                            onSelectionChange={this.setDrawingIdsToCreate}
                        >
                            <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                                <ToggleButton id="line">
                                    <Line />
                                </ToggleButton>
                                <Tooltip >
                                    Draw line
                                </Tooltip>
                            </TooltipTrigger>

                            <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                                <ToggleButton id="parallel">
                                    <Properties />
                                </ToggleButton>
                                <Tooltip >
                                    Draw parallel
                                </Tooltip>
                            </TooltipTrigger>

                            <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                                <ToggleButton id="gann_angles">
                                    <Collection />
                                </ToggleButton>
                                <Tooltip >
                                    Draw Gann angles
                                </Tooltip>
                            </TooltipTrigger>

                            <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                                <ToggleButton id="fibonacci_retrace" >
                                    <DistributeSpaceVertically />
                                </ToggleButton>
                                <Tooltip >
                                    Draw Fibonacci retrace
                                </Tooltip>
                            </TooltipTrigger>

                            <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                                <ToggleButton id="fibonacci_timezone">
                                    <DistributeSpaceHorizontally />
                                </ToggleButton>
                                <Tooltip >
                                    Draw Fibonacci time zone
                                </Tooltip>
                            </TooltipTrigger>

                            <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                                <ToggleButton id="fibonacci_retrace_v">
                                    <AudioWave />
                                </ToggleButton>
                                <Tooltip >
                                    Draw Fibonacci time retrace
                                </Tooltip>
                            </TooltipTrigger>

                            <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                                <ToggleButton id="polyline" >
                                    <DirectSelect />
                                </ToggleButton>
                                <Tooltip >
                                    Draw polyline
                                </Tooltip>
                            </TooltipTrigger>

                        </ToggleButtonGroup>

                        <Divider staticColor='auto' />

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={() => this.setState({
                                updateDrawing: {
                                    action: 'hide',
                                    isHidingDrawing: !this.state.updateDrawing.isHidingDrawing
                                }
                            })}
                            >
                                <SelectNo />
                            </ActionButton>
                            <Tooltip >
                                Hide drawings
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={() => this.setState({
                                updateDrawing: {
                                    ...(this.state.updateDrawing),
                                    action: 'delete'
                                }
                            })}
                            >
                                <SelectNone />
                            </ActionButton>
                            <Tooltip>
                                Delete selected drawing
                            </Tooltip>
                        </TooltipTrigger>

                        <Divider staticColor='auto' />

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.toggleKlineKind} >
                                <DistributeHorizontalCenter />
                            </ActionButton>
                            <Tooltip >
                                Toggle candle/bar chart
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.toggleScalar} >
                                <Percentage />
                            </ActionButton>
                            <Tooltip >
                                Toggle Linear/Lg scale
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.backToOriginalChartScale} >
                                <Maximize />
                            </ActionButton>
                            <Tooltip >
                                Original chart height
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.toggleCrosshairVisiable} >
                                <Add />
                            </ActionButton>
                            <Tooltip >
                                Toggle crosshair visible
                            </Tooltip>
                        </TooltipTrigger>

                        <Divider staticColor='auto' />

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.props.toggleColorTheme} >
                                <BrightnessContrast />
                            </ActionButton>
                            <Tooltip>
                                Toggle color theme
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={500} placement="end">
                            <DialogTrigger>
                                <ActionButton >
                                    <HelpCircle />
                                </ActionButton>
                                <Tooltip>
                                    Help
                                </Tooltip>

                                <Popover>
                                    <div className="help" >
                                        <Help />
                                    </div>
                                </Popover>
                            </DialogTrigger>
                        </TooltipTrigger>

                        <Divider staticColor='auto' />

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.handleForceRefresh} >
                                <Refresh />
                            </ActionButton>
                            <Tooltip>
                                Sync Latest Data
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.handleSyncWatchlistDaily} >
                                <Collection />
                            </ActionButton>
                            <Tooltip>
                                Sync Watchlist Daily
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={() => {
                                const baseUrl = import.meta.env.BASE_URL;
                                const target = baseUrl.endsWith('/') ? `${baseUrl}industry-templates` : `${baseUrl}/industry-templates`;
                                window.open(target, '_blank');
                            }} >
                                <Edit />
                            </ActionButton>
                            <Tooltip>
                                Industry Templates
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={() => {
                                const baseUrl = import.meta.env.BASE_URL;
                                const target = baseUrl.endsWith('/') ? `${baseUrl}screening` : `${baseUrl}/screening`;
                                window.open(target, '_blank');
                            }} >
                                <Filter />
                            </ActionButton>
                            <Tooltip>
                                AI Screening
                            </Tooltip>
                        </TooltipTrigger>

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={() => {
                                const baseUrl = import.meta.env.BASE_URL;
                                // Handle case where baseUrl is '/' or '/vibetrader/'
                                const target = baseUrl.endsWith('/') ? `${baseUrl}paper` : `${baseUrl}/paper`;
                                window.open(target, '_blank');
                            }} >
                                <Play />
                            </ActionButton>
                            <Tooltip>
                                Paper Trading Simulator
                            </Tooltip>
                        </TooltipTrigger>

                        <Divider staticColor='auto' />

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.handleTakeScreenshot} >
                                <Exposure />
                            </ActionButton>
                            <Tooltip>
                                Take screenshot
                            </Tooltip>
                        </TooltipTrigger>

                        <Divider staticColor='auto' />

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={this.toggleAIPanel} >
                                <StarFilled />
                            </ActionButton>
                            <Tooltip>
                                Toggle AI Panel
                            </Tooltip>
                        </TooltipTrigger>

                        <Divider staticColor='auto' />

                        <TooltipTrigger delay={TOOLTIP_DELAY} placement="end">
                            <ActionButton onPress={() => window.open(import.meta.env.BASE_URL + 'actions', '_blank')} >
                                <Prototyping />
                            </ActionButton>
                            <Tooltip>
                                Action Plan
                            </Tooltip>
                        </TooltipTrigger>

                    </ActionButtonGroup>

                </div>

                <div
                    ref={this.contentRef}
                    style={{
                    position: 'relative',
                    flex: 1,
                    minWidth: 0,
                    height: '100%',
                    overflowY: 'auto',
                    overflowX: 'hidden',
                    display: 'flex',
                    flexDirection: 'column'
                }}>
                    {this.state.isLoaded && (<>
                        <div className="title" style={{ width: this.width, height: this.hTitle, flexShrink: 0, paddingTop: '20px', boxSizing: 'border-box' }}>
                            <Title
                                symbol={this.symbol}
                                tvar={this.kvar}
                                width={this.width}
                                height={this.hTitle}
                                isAIPanelOpen={this.state.isAIPanelOpen}
                                toggleAIPanel={this.toggleAIPanel}

                                handleSymbolTimeframeChanged={this.handleSymbolTimeframeChanged}
                                xc={this.xc}
                                updateEvent={this.state.updateEvent}
                            />
                        </div>

                        <div className="" style={{
                            display: 'flex', justifyContent: 'flex-start',
                            width: this.width, height: this.hIndtags,
                            paddingTop: "0px",
                            position: 'relative', // Anchor for legend
                            zIndex: 101 // Ensure above chart
                        }}>
                            <TagGroup
                                aria-label="Indicator selection"
                                size="S"
                                selectionMode="multiple"
                                selectedKeys={this.state.selectedIndicatorTags}
                                onSelectionChange={this.setSelectedIndicatorTags}
                            >
                                {allIndTags.map((tag, n) =>
                                    <Tag key={"ind-tag-" + n} id={tag}>{tag.toUpperCase()}</Tag>
                                )}
                            </TagGroup>

                            {/* Legend for Overlay Indicators anchored below buttons */}
                            <div style={{
                                position: 'absolute',
                                top: '100%', // Directly below the buttons
                                left: 10,
                                fontSize: '12px',
                                display: 'flex',
                                flexDirection: 'column',
                                pointerEvents: 'none',
                                marginTop: '4px' // Small gap
                            }}>
                                {this.state.overlayIndicators?.map((ind, i) => (
                                    <div key={i} style={{ display: 'flex', gap: '12px', marginBottom: '2px' }}>
                                        {ind.outputs.map((out, j) => {
                                            const label = this.state.overlayIndicatorLabels?.[i]?.[j];
                                            return (
                                                <span key={j} style={{ color: out.options.color || '#F00', display: 'flex', gap: '4px', textShadow: '0px 0px 2px white' }}>
                                                    <span style={{ fontWeight: 600 }}>{out.title}</span>
                                                    {label && <span>{label}</span>}
                                                </span>
                                            );
                                        })}
                                    </div>
                                ))}
                            </div>
                        </div>



                        <div className="klineview" style={{ width: this.width, height: this.hKlineView, marginTop: this.hSpacing }}>
                            <svg className="annotations"
                                width={this.width}
                                height={this.hKlineView}
                                vectorEffect="non-scaling-stroke"
                                style={{ zIndex: 1 }}
                            >
                                <KlineView
                                    updateEvent={this.state.updateEvent}
                                    updateDrawing={this.state.updateDrawing}
                                    xc={this.xc}
                                    tvar={this.kvar}
                                    width={this.width}
                                    height={this.hKlineView}
                                    x={0}
                                    y={0}
                                    id="kline"
                                    name="kline"
                                    overlayIndicators={this.state.overlayIndicators}
                                    callbacksToContainer={this.callbacks}
                                />
                            </svg>
                        </div>
                        <div className="volumeview" style={{ width: this.width, height: this.hVolumeView, marginTop: this.hSpacing }}>
                            <svg
                                width={this.width}
                                height={this.hVolumeView}
                                vectorEffect="non-scaling-stroke"
                            >
                                <VolumeView
                                    updateEvent={this.state.updateEvent}
                                    xc={this.xc}
                                    tvar={this.kvar}
                                    width={this.width}
                                    height={this.hVolumeView}
                                    x={0}
                                    y={0}
                                    id="volume"
                                    name="volume"
                                />
                            </svg>
                        </div>
                        {this.state.stackedIndicators && this.state.stackedIndicators.map((indicator, n) => {
                            return (
                                <div className={this.#indicatorViewId(n)} style={{ width: this.width, height: this.hIndicatorView, marginTop: this.hSpacing }}
                                    key={this.#indicatorViewId(n)}
                                >
                                    <svg
                                        width={this.width}
                                        height={this.hIndicatorView}
                                        vectorEffect="non-scaling-stroke"
                                    >
                                        <IndicatorView
                                            updateEvent={this.state.updateEvent}
                                            xc={this.xc}
                                            width={this.width}
                                            height={this.hIndicatorView}
                                            x={0}
                                            y={0}
                                            id={this.#indicatorViewId(n)}
                                            name={this.#indicatorViewId(n)}
                                            tvar={indicator.tvar}
                                            mainIndicatorOutputs={indicator.outputs}

                                            indicator={indicator}
                                            indicatorLabels={this.state.stackedIndicatorLabels && this.state.stackedIndicatorLabels[n]}
                                            referIndicatorLabels={this.state.referStackedIndicatorLabels && this.state.referStackedIndicatorLabels[n]}
                                        />
                                    </svg>
                                </div>
                            )
                        }
                        )}
                        <div className="axisx" style={{ width: this.width, height: this.hAxisx, marginTop: this.hSpacing }}>
                            <svg
                                width={this.width}
                                height={this.hAxisx}
                                vectorEffect="non-scaling-stroke"
                                style={{ fontSize: '11px' }}
                            >
                                <AxisX
                                    updateEvent={this.state.updateEvent}
                                    xc={this.xc}
                                    width={this.width}
                                    height={this.hAxisx}
                                    x={0}
                                    y={0}
                                    id="axisx"
                                />
                            </svg>
                        </div>
                    </>)}

                    {this.state.screenshot && (
                        <Screenshot
                            canvas={this.state.screenshot}
                            onClose={() => { this.setState({ screenshot: undefined }) }}
                        />
                    )}

                </div>

                {this.state.isAIPanelOpen && (
                    <>
                        <div
                            onMouseDown={this.handleResizeMouseDown}
                            style={{
                                width: '4px',
                                cursor: 'col-resize',
                                backgroundColor: 'transparent',
                                zIndex: 100,
                                display: 'flex',
                                justifyContent: 'center',
                                alignItems: 'center'
                            }}
                            className="resize-handle"
                        >
                            <div style={{ width: '1px', height: '100%', backgroundColor: '#e0e0e0' }} />
                        </div>
                        <div style={{ width: this.state.aiPanelWidth, height: '100%' }}
                            onWheel={(e) => e.stopPropagation()}
                            onMouseDown={(e) => e.stopPropagation()}
                        >
                            <AIAnalysisPanel
                                symbol={this.symbol}
                                klines={this.kvar && this.kvar.toArray ? this.kvar.toArray().slice(-100) : []}
                                isOpen={this.state.isAIPanelOpen}
                                onClose={this.toggleAIPanel}
                            />
                        </div>
                    </>
                )}

                {this.state.toast && (
                    <div style={{
                        position: 'absolute',
                        top: '20px',
                        left: '50%',
                        transform: 'translateX(-50%)',
                        padding: '8px 16px',
                        borderRadius: '4px',
                        color: 'white',
                        backgroundColor: this.state.toast.type === 'error' ? '#e53e3e' :
                            this.state.toast.type === 'success' ? '#38a169' : '#3182ce',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                        zIndex: 1000,
                        fontSize: '14px',
                        fontWeight: 500,
                        pointerEvents: 'none'
                    }}>
                        {this.state.toast.message}
                    </div>
                )}
                {apiBannerMessage && (
                    <div style={{
                        position: 'absolute',
                        top: '8px',
                        left: '50%',
                        transform: 'translateX(-50%)',
                        padding: '4px 10px',
                        borderRadius: '4px',
                        color: 'white',
                        backgroundColor: '#d69e2e',
                        boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
                        zIndex: 1000,
                        fontSize: '11px',
                        fontWeight: 500,
                        lineHeight: 1.2,
                        pointerEvents: 'none'
                    }}>
                        {apiBannerMessage}
                    </div>
                )}
            </div>
        )
    }
}

export default KlineViewContainer;
