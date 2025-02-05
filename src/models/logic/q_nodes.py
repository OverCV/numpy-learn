import numpy as np
from src.funcs.base import emd_efecto, ABECEDARY, setup_logger
from src.middlewares.profile import profiler_manager, profile
from src.funcs.format import fmt_biparte_q
from controllers.manager import Manager
from src.models.base.sia import SIA

from src.models.core.solution import Solution
from src.constants.base import (
    ACTIVOS,
    EFECTO,
    ACTUAL,
    INFTY_NEG,
    INFTY_POS,
    INT_ONE,
    LAST_IDX,
    NEQ_SYM,
)


class QNodes(SIA):
    """
    Clase QNodes para el análisis de redes mediante el algoritmo Q.

    Esta clase implementa un gestor principal para el análisis de redes que utiliza
    el algoritmo Q para encontrar la partición óptima que minimiza la
    pérdida de información en el sistema. Hereda de la clase base SIA (Sistema de
    Información Activo) y proporciona funcionalidades para analizar la estructura
    y dinámica de la red.

    Args:
    ----
        config (Loader):
            Instancia de la clase Loader que contiene la configuración del sistema
            y los parámetros necesarios para el análisis.

    Attributes:
    ----------
        m (int):
            Número de elementos en el conjunto de purview (vista).

        n (int):
            Número de elementos en el conjunto de mecanismos.

        tiempos (tuple[np.ndarray, np.ndarray]):
            Tupla de dos arrays que representan los tiempos para los estados
            actual y efecto del sistema.

        etiquetas (list[tuple]):
            Lista de tuplas conteniendo las etiquetas para los nodos,
            con versiones en minúsculas y mayúsculas del abecedario.

        vertices (set[tuple]):
            Conjunto de vértices que representan los nodos de la red,
            donde cada vértice es una tupla (tiempo, índice).

        memoria (dict):
            Diccionario para almacenar resultados intermedios y finales
            del análisis (memoización).

        logger:
            Instancia del logger configurada para el análisis Q.

    Methods:
    -------
        run(conditions, purview, mechanism):
            Ejecuta el análisis principal de la red con las condiciones,
            purview y mecanismo especificados.

        algorithm(vertices):
            Implementa el algoritmo Q para encontrar la partición
            óptima del sistema.

        funcion_submodular(deltas, omegas):
            Calcula la función submodular para evaluar particiones candidatas.

        view_solution(mip):
            Visualiza la solución encontrada en términos de las particiones
            y sus valores asociados.

        nodes_complement(nodes):
            Obtiene el complemento de un conjunto de nodos respecto a todos
            los vértices del sistema.

    Notes:
    -----
    - La clase implementa una versión secuencial del algoritmo Q para encontrar la partición que minimiza la pérdida de información.
    - Utiliza memoización para evitar recálculos innecesarios durante el proceso.
    - El análisis se realiza considerando dos tiempos: actual (presente) y
      efecto (futuro).
    """

    def __init__(self, config: Manager):
        super().__init__(config)
        profiler_manager.start_session(
            f"NET{len(config.estado_inicial)}{config.pagina}"
        )
        self.m: int
        self.n: int
        self.tiempos: tuple[np.ndarray, np.ndarray]
        self.etiquetas = [tuple(s.lower() for s in ABECEDARY), ABECEDARY]
        self.vertices: set[tuple]
        self.memoria_delta = dict()
        self.memoria_particiones = dict()

        self.logger = setup_logger("q_strat")

    # @profile(context={"type": "q_analysis"})
    def aplicar_estrategia(self, conditions, purview, mechansim):
        self.sia_preparar_subsistema(conditions, purview, mechansim)

        # Pondremos el orden (tiempo, indice) con tiempo=1 el futuro, tiempo=0 el presente.
        purv = tuple((EFECTO, pur) for pur in self.sia_subsistema.indices_ncubos)
        mech = tuple((ACTUAL, mec) for mec in self.sia_subsistema.dims_ncubos)

        self.m = len(self.sia_subsistema.indices_ncubos)
        self.n = len(self.sia_subsistema.dims_ncubos)
        self.tiempos = (
            np.zeros(self.n, dtype=np.int8),
            np.zeros(self.m, dtype=np.int8),
        )

        vertices = list(mech + purv)
        self.vertices = set(mech + purv)
        mip = self.algorithm(vertices)

        fmt_mip = fmt_biparte_q(list(mip), self.nodes_complement(mip))

        return Solution(
            estrategia="Q-Nodes",
            perdida=self.memoria_particiones[mip][0],
            distribucion_subsistema=self.sia_dists_marginales,
            distribucion_particion=self.memoria_particiones[mip][1],
            particion=fmt_mip,
        )

    def algorithm(self, vertices: list[tuple[int, int]]):
        """
        Implementa el algoritmo Q para encontrar la partición óptima de un sistema que minimiza la pérdida de información, basándose en principios de submodularidad dentro de la teoría de lainformación.

        El algoritmo opera sobre un conjunto de vértices que representan nodos en diferentes tiempos del sistema (presente y futuro). La idea fundamental es construir incrementalmente grupos de nodos que, cuando se particionan, producen la menor pérdida posible de información en el sistema.

        Proceso Principal:
        -----------------
        El algoritmo comienza estableciendo dos conjuntos fundamentales: omega (W) y delta.
        Omega siempre inicia con el primer vértice del sistema, mientras que delta contiene todos los vértices restantes. Esta decisión no es arbitraria - al comenzar con un
        solo elemento en omega, podemos construir grupos de manera incremental evaluando cómo cada adición afecta la pérdida de información.

        La ejecución se desarrolla en fases, ciclos e iteraciones, donde cada fase representa un nivel diferente y conlleva a la formación de una partición candidata, cada ciclo representa un incremento de elementos al conjunto W y cada iteración determina al final cuál es el mejor elemento/cambio/delta para añadir en W.
        Fase >> Ciclo >> Iteración.

        1. Formación Incremental de Grupos:
        El algoritmo mantiene un conjunto omega que crece gradualmente en cada j-iteración. En cada paso, evalúa todos los deltas restantes para encontrar cuál, al unirse con omega produce la menor pérdida de información. Este proceso utiliza la función submodular para calcular la diferencia entre la EMD (Earth Mover's Distance) de la combinación y la EMD individual del delta evaluado.

        2. Evaluación de deltas:
        Para cada delta candidato el algoritmo:
        - Calcula su EMD individual si no está en memoria.
        - Calcula la EMD de su combinación con el conjunto omega actual
        - Determina la diferencia entre estas EMDs (el "costo" de la combinación)
        El delta que produce el menor costo se selecciona y se añade a omega.

        3. Formación de Nuevos Grupos:
        Al final de cada fase cuando omega crezca lo suficiente, el algoritmo:
        - Toma los últimos elementos de omega y delta (par candidato).
        - Los combina en un nuevo grupo
        - Actualiza la lista de vértices para la siguiente fase
        Este proceso de agrupamiento permite que el algoritmo construya particiones
        cada vez más complejas y reutilice estos "pares candidatos" para particiones en conjunto.

        Optimización y Memoria:
        ----------------------
        El algoritmo utiliza dos estructuras de memoria clave:
        - individual_memory: Almacena las EMDs y distribuciones de nodos individuales, evitando recálculos muy costosos.
        - partition_memory: Guarda las EMDs y distribuciones de las particiones completas, permitiendo comparar diferentes combinaciones de grupos teniendo en cuenta que su valor real está asociado al valor individual de su formación delta.

        La memoización es relevante puesto muchos cálculos de EMD son computacionalmente costosos y se repiten durante la ejecución del algoritmo.

        Resultado:
        ---------------
        Al terminar todas las fases, el algoritmo selecciona la partición que produjo la menor EMD global, representando la división del sistema que mejor preserva su información causal.

        Args:
            vertices (list[tuple[int, int]]): Lista de vértices donde cada uno es una
                tupla (tiempo, índice). tiempo=0 para presente (t_0), tiempo=1 para futuro (t_1).

        Returns:
            tuple[float, tuple[tuple[int, int], ...]]: El valor de pérdida en la primera posición, asociado con la partición óptima encontrada, identificada por la clave en partition_memory que produce la menor EMD.
        """
        omegas_origen = np.array([vertices[0]])
        deltas_origen = np.array(vertices[1:])

        vertices_fase = vertices

        omegas_ciclo = omegas_origen
        deltas_ciclo = deltas_origen
        self.logger.debug(omegas_ciclo, deltas_ciclo)

        for i in range(len(vertices_fase) - 2):
            self.logger.warn(f"\n{'≡' * 50}{i=}")
            self.logger.debug(
                f"FASE con nuevo grupo formado (si i{NEQ_SYM}0):\n\t{vertices_fase}"
            )

            omegas_ciclo = [vertices_fase[0]]
            deltas_ciclo = vertices_fase[1:]

            self.logger.debug(f"fase inicia con W: {omegas_ciclo}")

            emd_particion_candidata = INFTY_POS

            for j in range(len(deltas_ciclo) - 1):
                self.logger.warn(f"\n{'='*45}{j=}")
                self.logger.debug(f"CICLO W crece: {omegas_ciclo}")

                emd_local = 1e5
                indice_mip: int
                for k in range(len(deltas_ciclo)):
                    self.logger.warn(f"\n{'-'*40}{k=}")
                    self.logger.debug("ITER calculando cada delta")

                    emd_union, emd_delta, dist_marginal_delta = self.funcion_submodular(
                        deltas_ciclo[k], omegas_ciclo
                    )
                    emd_iteracion = emd_union - emd_delta

                    self.logger.debug(f"local: {emd_iteracion}, global: {emd_local}")
                    if emd_iteracion < emd_local:
                        emd_local = emd_iteracion
                        indice_mip = k

                        emd_particion_candidata = emd_delta
                        dist_particion_candidata = dist_marginal_delta
                    else:
                        emd_particion_candidata = emd_delta
                        dist_particion_candidata = dist_marginal_delta
                    ...

                omegas_ciclo.append(deltas_ciclo[indice_mip])
                deltas_ciclo.pop(indice_mip)

                self.logger.debug(
                    f"\nCICLO Minimo delta hallado:\n\t{deltas_ciclo[indice_mip]=}"
                )
                self.logger.debug("\tAñadir a ciclo omega. Quitándolo de deltas.")
                deltas_ciclo.pop(indice_mip)
                ...

            self.logger.debug("Añadir nueva partición entre ultimos de omega y delta")
            self.logger.debug(f"{omegas_ciclo, deltas_ciclo=}")

            self.memoria_particiones[
                tuple(
                    deltas_ciclo[LAST_IDX]
                    if isinstance(deltas_ciclo[LAST_IDX], list)
                    else deltas_ciclo
                )
            ] = emd_particion_candidata, dist_particion_candidata

            par_candidato = (
                [omegas_ciclo[LAST_IDX]]
                if isinstance(omegas_ciclo[LAST_IDX], tuple)
                else omegas_ciclo[LAST_IDX]
            ) + (
                deltas_ciclo[LAST_IDX]
                if isinstance(deltas_ciclo[LAST_IDX], list)
                else deltas_ciclo
            )

            self.logger.debug(f"{par_candidato=}")

            omegas_ciclo.pop()
            omegas_ciclo.append(par_candidato)

            self.logger.warn(
                f"\nGrupos partición obtenidos durante ejecucion:\n{(self.memoria_particiones)=}"
            )
            vertices_fase = omegas_ciclo
            ...

        return min(
            self.memoria_particiones, key=lambda k: self.memoria_particiones[k][0]
        )

    def funcion_submodular(
        self, deltas: tuple | list[tuple], omegas: list[tuple | list[tuple]]
    ):
        """
        Evalúa el impacto de combinar el conjunto de nodos individual delta y su agrupación con el conjunto omega, calculando la diferencia entre EMD (Earth Mover's Distance) de las configuraciones, en conclusión los nodos delta evaluados individualmente y su combinación con el conjunto omega.

        El proceso se realiza en dos fases principales:

        1. Evaluación Individual:
           - Crea una copia del estado temporal del subsistema.
           - Activa los nodos delta en su tiempo correspondiente (presente/futuro).
           - Si el delta ya fue evaluado antes, recupera su EMD y distribución marginal de memoria
           - Si no, ha de:
             * Identificar dimensiones activas en presente y futuro.
             * Realiza bipartición del subsistema con esas dimensiones.
             * Calcular la distribución marginal y EMD respecto al subsistema.
             * Guarda resultados en memoria para seguro un uso futuro.

        2. Evaluación Combinada:
           - Sobre la misma copia temporal, activa también los nodos omega.
           - Calcula dimensiones activas totales (delta + omega).
           - Realiza bipartición del subsistema completo.
           - Obtiene EMD de la combinación.

        Args:
            deltas: Un nodo individual (tupla) o grupo de nodos (lista de tuplas)
                   donde cada tupla está identificada por su (tiempo, índice), sea el tiempo t_0 identificado como 0, t_1 como 1 y, el índice hace referencia a las variables/dimensiones habilitadas para operaciones de substracción/marginalización sobre el subsistema, tal que genere la partición.
            omegas: Lista de nodos ya agrupados, puede contener tuplas individuales
                   o listas de tuplas para grupos formados por los pares candidatos o más uniones entre sí (grupos candidatos).

        Returns:
            tuple: (
                EMD de la combinación omega y delta,
                EMD del delta individual,
                Distribución marginal del delta individual
            )
            Esto lo hice así para hacer almacenamiento externo de la emd individual y su distribución marginal en las particiones candidatas.
        """
        tiempos = np.copy(self.tiempos)
        emd_delta = INFTY_NEG

        self.logger.debug(f"{deltas=}")

        if isinstance(deltas, tuple):
            d_tiempo, d_indice = deltas
            tiempos[d_tiempo][d_indice] = ACTIVOS
        else:
            for delta in deltas:
                d_tiempo, d_indice = delta
                tiempos[d_tiempo][d_indice] = ACTIVOS

        self.logger.debug(f"{self.sia_dists_marginales=}")

        if tuple(deltas) in self.memoria_delta:
            emd_delta, vector_delta_marginal = self.memoria_delta[tuple(deltas)]
        else:
            copia_delta = self.sia_subsistema

            dims_alcance_delta = tuple(
                idx for idx, bit in enumerate(tiempos[EFECTO]) if bit == INT_ONE
            )
            dims_mecanismo_delta = tuple(
                idx for idx, bit in enumerate(tiempos[ACTUAL]) if bit == INT_ONE
            )

            particion_delta = copia_delta.bipartir(
                np.array(dims_alcance_delta, dtype=np.int8),
                np.array(dims_mecanismo_delta, dtype=np.int8),
            )
            vector_delta_marginal = particion_delta.distribucion_marginal()
            emd_delta = emd_efecto(vector_delta_marginal, self.sia_dists_marginales)

            self.memoria_delta[tuple(deltas)] = emd_delta, vector_delta_marginal

        self.logger.info(f"{particion_delta}")
        self.logger.debug(f"{vector_delta_marginal=}")
        self.logger.info(f"{tiempos[EFECTO], tiempos[ACTUAL]=}")
        self.logger.info(f"{emd_delta}")

        # Unión #

        self.logger.debug(f"{omegas=}")

        for omega in omegas:
            if isinstance(omega, list):
                for omg in omega:
                    o_time, o_index = omg
                    tiempos[o_time][o_index] = ACTIVOS
            else:
                o_time, o_index = omega
                tiempos[o_time][o_index] = ACTIVOS

        self.logger.debug(f"{self.sia_dists_marginales=}")

        copia_union = self.sia_subsistema

        dims_alcance_union = tuple(
            idx for idx, bit in enumerate(tiempos[EFECTO]) if bit == INT_ONE
        )
        dims_mecanismo_union = tuple(
            idx for idx, bit in enumerate(tiempos[ACTUAL]) if bit == INT_ONE
        )

        particion_union = copia_union.bipartir(
            np.array(dims_alcance_union, dtype=np.int8),
            np.array(dims_mecanismo_union, dtype=np.int8),
        )
        vector_union_marginal = particion_union.distribucion_marginal()
        emd_union = emd_efecto(vector_union_marginal, self.sia_dists_marginales)

        self.logger.info(f"{particion_union}")
        self.logger.debug(f"{vector_union_marginal=}")
        self.logger.info(f"{tiempos[EFECTO], tiempos[ACTUAL]=}")
        self.logger.info(f"{emd_union}")

        self.logger.debug(f"{emd_union - emd_delta}={emd_union}-{emd_delta}")

        return emd_union, emd_delta, vector_delta_marginal

    def nodes_complement(self, nodes: list[tuple[int, int]]):
        return list(set(self.vertices) - set(nodes))
